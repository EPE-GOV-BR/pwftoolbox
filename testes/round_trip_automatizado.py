"""Verificacao autonoma: python testes/round_trip_automatizado.py.

Usa o deck publico v12 e registros sinteticos v13, sem framework de testes.
Os casos sinteticos nao certificam compatibilidade com um deck real v13.
"""

from importlib.resources import as_file, files
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd

from pwftoolbox import carrega_pwf, escreve_pwf
from round_trip_pwf import DEFAULT_ATOL, DEFAULT_INPUT, _compare_blocks


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def _check_round_trip(original: dict, output: Path, json_path: Path | None = None) -> list[str]:
    snapshots = {
        code: frame.copy(deep=True)
        for code, frame in original.items() if isinstance(frame, pd.DataFrame)
    }
    escreve_pwf(original, pwf_path=output, json_path=json_path)
    for code, snapshot in snapshots.items():
        pd.testing.assert_frame_equal(original[code], snapshot)
    generated = carrega_pwf(output, json_path=json_path)
    errors, _, compared = _compare_blocks(original, generated, DEFAULT_ATOL)
    _require(not errors, "Round-trip divergente: " + "; ".join(errors))
    return compared


def _fixed_record(width: int, fields: list[tuple[int, int, str]]) -> str:
    record = [' '] * width
    for start, end, token in fields:
        _require(len(token) <= end - start, "Fixture excede largura do campo")
        record[start:end] = token.rjust(end - start)
    return ''.join(record)


def _check_v13(directory: Path) -> None:
    delo = _fixed_record(45, [
        (0, 4, '1'), (5, 6, 'A'), (7, 12, '500.'), (13, 18, '1000.'),
        (19, 39, 'ELO TESTE'), (40, 41, 'N'), (42, 43, 'L'), (44, 45, 'V'),
    ])
    dvsc = _fixed_record(91, [
        (0, 4, '1'), (5, 6, 'A'), (7, 8, 'L'), (9, 14, '10'),
        (15, 19, '20'), (20, 24, '30'), (25, 26, 'R'), (27, 34, '1000.'),
        (35, 41, '-100.'), (41, 47, '100.'), (47, 59, 'CURVA_TESTE'),
        (60, 66, '.25'), (67, 73, '.5'), (74, 79, '.1'),
        (79, 85, '200.'), (85, 91, '230.'),
    ])
    expected = {
        'DELO': {'Num': 1, 'O': 'A', 'V': 500.0, 'P': 1000.0,
                 'Nome': 'ELO TESTE', 'M': 'N', 'E': 'L', 'T': 'V'},
        'DVSC': {'No': 1, 'O': 'A', 'E': 'L', 'CA': 10, 'CC': 20, 'EL': 30,
                 'T': 'R', 'Esp.': 1000.0, 'Qmin': -100.0, 'Qmax': 100.0,
                 'IdCurvaPts': 'CURVA_TESTE', 'Rarm': .25, 'Rtrf': .5,
                 'Xtrf': .1, 'SbsT': 200.0, 'VbsS': 230.0},
    }
    source = directory / 'sintetico_v13.PWF'
    source.write_text(f'DELO\n{delo}\n99999\nDVSC\n{dvsc}\n99999\nFIM\n', encoding='latin-1')
    with as_file(files('pwftoolbox').joinpath('pwf_specs_v13.json')) as specs:
        original = carrega_pwf(source, json_path=specs)
        for code, values in expected.items():
            frame = original[code]
            _require(isinstance(frame, pd.DataFrame) and len(frame) == 1,
                     f'{code}: registro sintetico nao foi lido')
            _require(list(frame.columns) == list(values), f'{code}: colunas inesperadas')
            for name, value in values.items():
                dtype = 'Int64' if isinstance(value, int) else 'Float64' if isinstance(value, float) else 'string'
                _require(str(frame[name].dtype) == dtype, f'{code}.{name}: tipo inesperado')
                _require(frame.at[0, name] == value, f'{code}.{name}: valor inesperado')
        compared = _check_round_trip(original, directory / 'v13_out.PWF', specs)
    _require(compared == ['DELO', 'DVSC'], 'Blocos sinteticos v13 nao foram comparados')
    print('[OK] v13: DELO/DVSC sinteticos; sem certificacao de deck real.')


def _check_reader_errors(directory: Path) -> None:
    source = directory / 'leitura.PWF'
    source.write_text('DGBT\n\n( comentario\n 1  230.\n99999\nFIM\nDGBT\n', encoding='latin-1')
    valid = carrega_pwf(source)
    _require(valid['DGBT'].at[0, 'kV'] == 230.0, 'Linha vazia ou FIM tratados incorretamente')
    for text, reason in [
        ('DGBT\n 1  abc\n99999\nFIM\n', 'converter'),
        ('DGBT\n    230.\n99999\nFIM\n', 'obrigat'),
        ('DGBT\n 1  230.\nFIM\n', 'terminador'),
        ('DGBT\n 1  230.\n', 'terminador'),
        ('DGBT\n 1  230.\nDBAR\n', 'terminador'),
    ]:
        source.write_text(text, encoding='latin-1')
        try:
            carrega_pwf(source)
        except ValueError as exc:
            message = str(exc)
            _require(all(token in message for token in (str(source), 'DGBT', 'linhas', reason)),
                     'Erro de leitura sem contexto suficiente: ' + message)
            if reason != 'terminador':
                _require(exc.__cause__ is not None, 'Causa original do erro perdida')
        else:
            raise RuntimeError('Leitura invalida nao levantou ValueError')
    lost = dict(valid)
    lost['DGBT'] = []
    for left, right in [(valid, lost), (lost, valid)]:
        errors, details, _ = _compare_blocks(left, right)
        _require(bool(errors) and any(item.get('issue') == 'block_presence_mismatch' for item in details),
                 'Comparador ignorou bloco perdido')
    print('[OK] Linhas vazias, FIM, erros contextuais e deteccao de blocos perdidos.')


def main() -> int:
    with TemporaryDirectory(prefix='pwftoolbox_') as temporary:
        directory = Path(temporary)
        _check_reader_errors(directory)
        _check_v13(directory)
        original = carrega_pwf(DEFAULT_INPUT)
        compared = _check_round_trip(original, directory / 'v12_out.PWF')
        _require({'DBAR', 'DLIN'}.issubset(compared), 'Deck publico sem DBAR/DLIN comparados')
        _require(original['TITU'] == [], 'TITU deixou de seguir a limitacao documentada')
        print(f'[OK] Deck publico v12: {len(compared)} blocos, atol={DEFAULT_ATOL}; TITU excluido.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
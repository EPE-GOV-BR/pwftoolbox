# -*- coding: utf-8 -*-
"""
Script de round-trip para arquivos .PWF.

Uso:
    python testes/round_trip_pwf.py
    python testes/round_trip_pwf.py <entrada.pwf> <saida.pwf> [specs.json] [relatorio.json] [log.txt]

Opcional: --atol=<valor> altera a tolerancia absoluta da comparacao numerica
(padrao 1e-6).
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd


ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = ROOT_DIR / "testes" / "entrada" / "2036_PDE35.PWF"
DEFAULT_OUTPUT = ROOT_DIR / "testes" / "saida" / "ROUND_TRIP_OUT.PWF"
DEFAULT_REPORT = ROOT_DIR / "testes" / "saida" / "relatorio.json"
DEFAULT_LOG = ROOT_DIR / "testes" / "saida" / "log.txt"
DEFAULT_ATOL = 1e-6

from pwftoolbox import parser_pwf


class _Tee:
    """Encaminha a saida escrita para o terminal original e para um arquivo de log."""

    def __init__(self, *streams):
        self._streams = streams

    def write(self, data):
        for stream in self._streams:
            stream.write(data)

    def flush(self):
        for stream in self._streams:
            stream.flush()


def _normalize_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """Normaliza DataFrame para comparação determinística de round-trip.

    Args:
        df: DataFrame a normalizar.

    Returns:
        DataFrame com colunas numéricas coercidas e arredondadas, colunas de
        texto sem espaços nas bordas e linhas ordenadas por todas as colunas.
    """
    normalized = df.copy()
    for col in normalized.columns:
        if pd.api.types.is_numeric_dtype(normalized[col]):
            normalized[col] = pd.to_numeric(normalized[col], errors="coerce").round(8)
        else:
            normalized[col] = normalized[col].fillna("").astype(str).str.strip()
    sort_cols = list(normalized.columns)
    if sort_cols:
        normalized = normalized.sort_values(by=sort_cols, kind="mergesort").reset_index(drop=True)
    return normalized


def _build_numeric_field_report(cod_exec: str, col: str, col_left: pd.Series, col_right: pd.Series, atol: float) -> dict | None:
    """Gera relatório de divergência para campo numérico.

    Args:
        cod_exec: Código de execução do bloco analisado.
        col: Nome da coluna em comparação.
        col_left: Série da referência (arquivo original).
        col_right: Série do round-trip (arquivo regravado).
        atol: Tolerância absoluta para diferença numérica.

    Returns:
        Dicionário com métricas de divergência e amostras quando houver
        diferenças; None quando a coluna está equivalente.
    """
    left_values = pd.to_numeric(col_left, errors="coerce").to_numpy(dtype=float)
    right_values = pd.to_numeric(col_right, errors="coerce").to_numpy(dtype=float)

    same_nan_mask = np.isnan(left_values) & np.isnan(right_values)
    both_numeric_mask = ~np.isnan(left_values) & ~np.isnan(right_values)
    diff_values = np.abs(left_values - right_values)
    diff_mask = both_numeric_mask & (diff_values > atol)
    nan_mismatch_mask = np.isnan(left_values) ^ np.isnan(right_values)
    mismatch_mask = diff_mask | nan_mismatch_mask

    mismatch_count = int(np.count_nonzero(mismatch_mask))
    if mismatch_count == 0:
        return None

    numeric_diffs = diff_values[diff_mask]
    max_abs_diff = float(np.nanmax(numeric_diffs)) if numeric_diffs.size else None
    mean_abs_diff = float(np.nanmean(numeric_diffs)) if numeric_diffs.size else None

    mismatch_indices = np.flatnonzero(mismatch_mask)
    samples = []
    for idx in mismatch_indices[:5]:
        lv = left_values[idx]
        rv = right_values[idx]
        if np.isnan(lv):
            left_repr = None
        else:
            left_repr = float(lv)
        if np.isnan(rv):
            right_repr = None
        else:
            right_repr = float(rv)
        sample = {
            "row": int(idx),
            "left": left_repr,
            "right": right_repr,
            "abs_diff": None if (left_repr is None or right_repr is None) else float(abs(left_repr - right_repr)),
        }
        samples.append(sample)

    return {
        "code": cod_exec,
        "field": col,
        "kind": "numeric",
        "rows": int(len(col_left)),
        "mismatch_count": mismatch_count,
        "atol": atol,
        "max_abs_diff": max_abs_diff,
        "mean_abs_diff": mean_abs_diff,
        "samples": samples,
    }


def _build_text_field_report(cod_exec: str, col: str, col_left: pd.Series, col_right: pd.Series) -> dict | None:
    """Gera relatório de divergência para campo textual.

    Args:
        cod_exec: Código de execução do bloco analisado.
        col: Nome da coluna em comparação.
        col_left: Série da referência (arquivo original).
        col_right: Série do round-trip (arquivo regravado).

    Returns:
        Dicionário com contagem e amostras de divergência textual; None quando
        a coluna está equivalente.
    """
    left_text = col_left.astype(str).fillna("")
    right_text = col_right.astype(str).fillna("")
    mismatch_mask = left_text != right_text
    mismatch_count = int(mismatch_mask.sum())
    if mismatch_count == 0:
        return None

    mismatch_indices = np.flatnonzero(mismatch_mask.to_numpy())
    samples = []
    for idx in mismatch_indices[:5]:
        samples.append({
            "row": int(idx),
            "left": left_text.iloc[idx],
            "right": right_text.iloc[idx],
        })

    return {
        "code": cod_exec,
        "field": col,
        "kind": "text",
        "rows": int(len(col_left)),
        "mismatch_count": mismatch_count,
        "samples": samples,
    }


def _compare_dataframes(cod_exec: str, original: pd.DataFrame, roundtrip: pd.DataFrame, atol: float = 1e-6) -> tuple[list[str], list[dict]]:
    """Compara dois DataFrames de um mesmo bloco PWF.

    Args:
        cod_exec: Código de execução do bloco comparado.
        original: DataFrame obtido da leitura do arquivo de entrada.
        roundtrip: DataFrame obtido da releitura do arquivo escrito.
        atol: Tolerância absoluta para diferenças em campos numéricos.

    Returns:
        Tupla (erros, detalhes), em que erros é uma lista de mensagens
        resumidas e detalhes contém relatórios estruturados por campo.
    """
    erros = []
    detalhes = []

    if list(original.columns) != list(roundtrip.columns):
        erros.append(f"{cod_exec}: colunas diferentes")
        detalhes.append({
            "code": cod_exec,
            "kind": "schema",
            "issue": "columns_mismatch",
            "left_columns": list(original.columns),
            "right_columns": list(roundtrip.columns),
        })
        return erros, detalhes

    if len(original) != len(roundtrip):
        erros.append(f"{cod_exec}: quantidade de linhas diferente ({len(original)} != {len(roundtrip)})")
        detalhes.append({
            "code": cod_exec,
            "kind": "schema",
            "issue": "row_count_mismatch",
            "left_rows": int(len(original)),
            "right_rows": int(len(roundtrip)),
        })
        return erros, detalhes

    left = _normalize_dataframe(original)
    right = _normalize_dataframe(roundtrip)

    for col in left.columns:
        col_left = left[col]
        col_right = right[col]

        if pd.api.types.is_integer_dtype(col_left) and pd.api.types.is_integer_dtype(col_right):
            field_report = _build_text_field_report(cod_exec, col, col_left, col_right)
            if field_report is not None:
                erros.append(f"{cod_exec}: diferenca inteira na coluna {col}")
                detalhes.append(field_report)
        elif pd.api.types.is_numeric_dtype(col_left) and pd.api.types.is_numeric_dtype(col_right):
            field_report = _build_numeric_field_report(cod_exec, col, col_left, col_right, atol)
            if field_report is not None:
                erros.append(f"{cod_exec}: diferenca numerica na coluna {col}")
                detalhes.append(field_report)
        else:
            field_report = _build_text_field_report(cod_exec, col, col_left, col_right)
            if field_report is not None:
                erros.append(f"{cod_exec}: diferenca textual na coluna {col}")
                detalhes.append(field_report)

    return erros, detalhes


def _compare_blocks(original: dict, generated: dict, atol: float = DEFAULT_ATOL) -> tuple[list[str], list[dict], list[str]]:
    """Compara blocos presentes, incluindo perdas e alterações de tipos."""
    erros = []
    detalhes = []
    comparados = []
    for codigo in sorted(set(original) | set(generated)):
        left = original.get(codigo)
        right = generated.get(codigo)
        left_present = isinstance(left, pd.DataFrame)
        right_present = isinstance(right, pd.DataFrame)
        if left_present != right_present:
            erros.append(f"{codigo}: presenca do bloco diferente")
            detalhes.append({
                "code": codigo, "kind": "schema", "issue": "block_presence_mismatch",
                "left_present": left_present, "right_present": right_present,
            })
        elif left_present and right_present:
            comparados.append(codigo)
            if not left.dtypes.equals(right.dtypes):
                erros.append(f"{codigo}: tipos de colunas diferentes")
                detalhes.append({
                    "code": codigo, "kind": "schema", "issue": "dtypes_mismatch",
                    "left_dtypes": left.dtypes.astype(str).to_dict(),
                    "right_dtypes": right.dtypes.astype(str).to_dict(),
                })
            erros_cod, detalhes_cod = _compare_dataframes(codigo, left, right, atol)
            erros.extend(erros_cod)
            detalhes.extend(detalhes_cod)
    if not comparados:
        erros.append("Nenhum bloco foi comparado")
        detalhes.append({"kind": "schema", "issue": "no_blocks_compared"})
    return erros, detalhes, comparados


def _write_report(report_path: Path, report_data: dict) -> None:
    """Persiste em JSON o relatório de divergências do round-trip.

    Args:
        report_path: Caminho do arquivo de relatório.
        report_data: Estrutura serializável com o resultado da comparação.
    """
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with report_path.open("w", encoding="utf-8") as fp:
        json.dump(report_data, fp, ensure_ascii=False, indent=2)


def run_round_trip(input_path: Path, output_path: Path, report_path: Path, json_path: Path | None = None,
                   atol: float = DEFAULT_ATOL) -> int:
    """Executa ciclo completo de round-trip e valida equivalência dos blocos.

    Args:
        input_path: Caminho do arquivo .PWF de entrada.
        output_path: Caminho do arquivo .PWF regravado.
        report_path: Caminho do relatório JSON de divergências.
        json_path: Caminho do JSON de specs (versão do ANAREDE); usa o default
            do parser (pwf_specs_v12.json) quando None.
        atol: Tolerância absoluta usada na comparação de campos numéricos.

    Returns:
        0 quando não há divergências; 1 quando há divergências nos blocos
        comparáveis.
    """
    print(f"[INFO] Lendo arquivo de entrada: {input_path}")
    original = parser_pwf.carrega_pwf(str(input_path), json_path=json_path)

    print(f"[INFO] Escrevendo arquivo de round-trip: {output_path}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    parser_pwf.escreve_pwf(original, pwf_path=str(output_path), json_path=json_path)

    print(f"[INFO] Relendo arquivo gerado: {output_path}")
    generated = parser_pwf.carrega_pwf(str(output_path), json_path=json_path)

    erros, detalhes, codigos = _compare_blocks(original, generated, atol)

    report_data = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "input_file": str(input_path),
        "output_file": str(output_path),
        "codes_compared": codigos,
        "atol": atol,
        "total_divergent_fields": int(len(detalhes)),
        "divergences": detalhes,
    }
    _write_report(report_path, report_data)
    print(f"[INFO] Relatorio salvo em: {report_path}")

    if erros:
        print("[ERRO] Round-trip falhou com as seguintes divergencias:")
        for erro in erros:
            print(f" - {erro}")
        return 1

    print("[OK] Round-trip concluido sem divergencias estruturais.")
    return 0


def main() -> int:
    """Ponto de entrada CLI para o teste de round-trip.

    Returns:
        Código de saída do processo:
        - 0: sucesso sem divergências;
        - 1: divergências encontradas;
        - 2: arquivo de entrada inexistente.
    """
    argv = sys.argv[1:]
    atol = DEFAULT_ATOL
    posicionais = []
    for arg in argv:
        if arg.startswith("--atol="):
            atol = float(arg.split("=", 1)[1])
        else:
            posicionais.append(arg)

    input_path = Path(posicionais[0]) if len(posicionais) > 0 else DEFAULT_INPUT
    output_path = Path(posicionais[1]) if len(posicionais) > 1 else DEFAULT_OUTPUT
    json_path = Path(posicionais[2]) if len(posicionais) > 2 else None
    report_path = Path(posicionais[3]) if len(posicionais) > 3 else DEFAULT_REPORT
    log_path = Path(posicionais[4]) if len(posicionais) > 4 else DEFAULT_LOG

    log_path.parent.mkdir(parents=True, exist_ok=True)
    original_stdout = sys.stdout
    with log_path.open("w", encoding="utf-8") as log_file:
        sys.stdout = _Tee(original_stdout, log_file)
        try:
            if not input_path.exists():
                print(f"[ERRO] Arquivo de entrada nao encontrado: {input_path}")
                return 2

            return run_round_trip(input_path, output_path, report_path, json_path, atol)
        finally:
            sys.stdout = original_stdout


if __name__ == "__main__":
    raise SystemExit(main())

# -*- coding: utf-8 -*-
"""Leitura e escrita de arquivos .PWF do ANAREDE.

O módulo converte blocos do formato PWF em pandas DataFrames e faz o caminho
inverso para serialização em largura fixa.
"""
import io
import json
import logging
import pandas as pd
import numpy as np
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_SPECS_FILENAME = 'pwf_specs_v12.json'

# Ordem canonica de escrita dos blocos (a ordem das chaves do dicionario
# devolvido por carrega_pwf define a ordem de escrita em escreve_pwf).
BLOCK_WRITE_ORDER = (
    'TITU', 'DAGR', 'DOPC', 'DCTE', 'DBAR', 'DGER', 'DLIN',
    'DCSC', 'DBSH', 'DSHL', 'DCER', 'DCTR', 'DGLT', 'DARE',
    'DTPF', 'DELO', 'DCBA', 'DCLI', 'DCNV', 'DCCV', 'DVSC',
    'DCAI', 'DGEI', 'DGBT', 'DCMT',
)

def _parse_colspec(colspec_text):
    """Converte colspec no formato objeto start/end para tupla inteira."""
    if not isinstance(colspec_text, dict):
        raise ValueError(f"colspec invalido: esperado objeto, recebido {colspec_text}")

    if 'start' not in colspec_text or 'end' not in colspec_text:
        raise ValueError(f"colspec invalido: esperado chaves start/end, recebido {colspec_text}")

    inicio = int(colspec_text['start'])
    fim = int(colspec_text['end'])
    if fim <= inicio:
        raise ValueError(f"colspec invalido: {colspec_text}")
    return (inicio, fim)


def _load_specs_from_json(json_path=None):
    """Carrega o schema JSON canonico e materializa estruturas de runtime.

    Returns:
        Tupla (FWF_SPECS, COD_EXEC, BLOCK_RULES) no formato usado pelo parser.
    """
    if json_path is None:
        json_path = Path(__file__).resolve().with_name(DEFAULT_SPECS_FILENAME)
    else:
        json_path = Path(json_path)

    with open(json_path, 'r', encoding='utf-8') as fp:
        payload = json.load(fp)

    blocks = payload.get('cod_exec')
    if not isinstance(blocks, dict) or not blocks:
        raise ValueError('Schema JSON invalido: cod_exec deve ser objeto nao vazio indexado por codigo.')

    fwf_specs = {}
    block_rules = {}
    cod_exec = []

    for code, block in blocks.items():
        if not isinstance(code, str) or not code:
            raise ValueError(f"Bloco invalido: chave de codigo ausente/invalida: {code}")
        if not isinstance(block, dict):
            raise ValueError(f"Bloco invalido para {code}: esperado objeto, recebido {type(block)}")
        if code in fwf_specs:
            raise ValueError(f"Codigo duplicado no JSON: {code}")

        fields = block.get('fields', {})
        if not isinstance(fields, dict):
            raise ValueError(f"Bloco {code} com fields invalido: esperado objeto")

        names = []
        colspecs = []
        field_meta = {}

        for field_code, field in fields.items():
            if not isinstance(field_code, str) or not field_code:
                raise ValueError(f"Campo invalido em {code}: chave de campo ausente/invalida")
            if not isinstance(field, dict):
                raise ValueError(f"Campo invalido em {code}.{field_code}: esperado objeto")
            if field_code in field_meta:
                raise ValueError(f"Campo duplicado em {code}: {field_code}")

            logical_type = field.get('type', 'str')
            if logical_type not in ('str', 'int', 'float'):
                raise ValueError(f"type invalido em {code}.{field_code}: {logical_type}")

            names.append(field_code)
            colspecs.append(_parse_colspec(field.get('colspec')))
            field_meta[field_code] = {
                'logical_type': logical_type,
                'implicit_scale': field.get('implicit_scale', None),
                'nullable': not bool(field.get('required', False)),
                'preserve_trailing_dot': bool(
                    field.get('preserve_trailing_dot', field.get('preserve_trailing_dots', False))
                ),
                'allow_scientific': bool(field.get('allow_scientific', True)),
                'name': field.get('name', field_code),
                'description': field.get('description', ''),
                'default': field.get('default', None),
            }

        if names:
            fwf_specs[code] = {
                'colspecs': colspecs,
                'names': tuple(names),
                'field_meta': field_meta,
            }
        block_rules[code] = {
            'has_terminator': bool(block.get('has_terminator', True)),
            'header_suffix': block.get('header_suffix', ''),
            'multiline_separator': block.get('multiline_separator', None),
            'header_field_count': block.get('header_field_count', None),
        }

        if block_rules[code]['multiline_separator'] and block_rules[code]['header_field_count'] is None:
            raise ValueError(
                f"Regra multiline invalida em {code}: header_field_count obrigatorio"
            )

        cod_exec.append(code)

    return fwf_specs, tuple(cod_exec), block_rules


def _load_runtime_specs(json_path=None):
    """Carrega o schema JSON e deriva todas as estruturas usadas em runtime.

    Args:
        json_path: Caminho para o JSON de specs; usa pwf_specs_v12.json ao lado
            deste módulo quando None.

    Returns:
        Dicionário com fwf_specs, cod_exec, block_rules, field_lengths e
        field_gaps, prontos para uso por carrega_pwf/escreve_pwf.
    """
    fwf_specs, cod_exec, block_rules = _load_specs_from_json(json_path)
    field_lengths = {cod: _campo_larguras(spec['colspecs']) for cod, spec in fwf_specs.items()}
    field_gaps = {cod: _campo_espacos(spec['colspecs']) for cod, spec in fwf_specs.items()}
    return {
        'fwf_specs': fwf_specs,
        'cod_exec': cod_exec,
        'block_rules': block_rules,
        'field_lengths': field_lengths,
        'field_gaps': field_gaps,
    }



def _preenche_colunas_faltantes(dataframe, lista_colunas):
    """Garante que o DataFrame tenha todas as colunas esperadas em ordem.

    Args:
        dataframe: DataFrame de entrada a ser alinhado ao layout esperado.
        lista_colunas: Sequência com os nomes de coluna esperados.

    Returns:
        DataFrame com todas as colunas de lista_colunas; colunas ausentes são
        criadas com None e a ordem final segue lista_colunas.
    """
    colunas_faltantes = [coluna for coluna in lista_colunas if coluna not in dataframe.columns]
    
    if colunas_faltantes:
        for coluna in colunas_faltantes:
            dataframe[coluna] = None
    dataframe = dataframe[list(lista_colunas)]
    return dataframe


def _campo_larguras(colspecs):
    """Converte colspecs em larguras de campos de largura fixa.

    Args:
        colspecs: Sequência de tuplas (inicio, fim) por campo.

    Returns:
        Lista de inteiros com a largura de cada campo (fim - inicio).
    """
    return [fim - inicio for inicio, fim in colspecs]


def _campo_espacos(colspecs):
    """Calcula espaçamentos entre campos consecutivos de um layout FWF.

    Args:
        colspecs: Sequência de tuplas (inicio, fim) por campo.

    Returns:
        Lista de strings com os espaços entre um campo e o próximo. O último
        campo recebe string vazia por não haver separador à direita.
    """
    espacos = [' ' * (colspecs[i + 1][0] - colspecs[i][1])
               for i in range(len(colspecs) - 1)]
    espacos.append('')
    return espacos


def _is_blank(value):
    """Informa se um valor deve ser tratado como vazio no parsing.

    Args:
        value: Valor escalar potencialmente vazio.

    Returns:
        True para strings em branco e sentinelas nulas reconhecidas por
        pandas/NumPy (NaN, NA, NaT); False para os demais casos.
    """
    # Empty strings
    if isinstance(value, str) and value.strip() == '':
        return True

    # Pandas/NumPy missing values: np.nan, pd.NA, NaT, etc.
    try:
        return bool(pd.isna(value))
    except TypeError:
        # Fallback for odd scalar types
        return False

def _coerce_series_to_schema(series, field_meta):
    """Converte uma Series para o tipo lógico definido em field_meta.

    Args:
        series: Série com valores já tokenizados de um campo PWF.
        field_meta: Metadados do campo (logical_type, implicit_scale etc.).

    Returns:
        Series tipada conforme logical_type:
        - int: pandas Int64 (nulo permitido), descartando frações inválidas.
        - float: pandas Float64.
        - str: pandas string.
    """
    logical_type = field_meta.get('logical_type', 'str')

    if logical_type == 'int':
        numeric = pd.to_numeric(series, errors='coerce')

        # Any non-integer numeric value in an int field becomes NA.
        frac_mask = numeric.notna() & ((numeric % 1) != 0)
        if frac_mask.any():
            numeric.loc[frac_mask] = np.nan

        return numeric.astype('Int64')

    if logical_type == 'float':
        return pd.to_numeric(series, errors='coerce').astype('Float64')

    return series.astype('string')


def _parse_field_value(raw_value, field_meta):
    """Converte um valor bruto conforme metadados de campo do layout PWF.

    Args:
        raw_value: Valor textual lido do arquivo em largura fixa.
        field_meta: Metadados do campo (tipo lógico e escala implícita).

    Returns:
        Valor convertido para int, float, string ou NaN quando vazio.

    Raises:
        ValueError: Se o valor não puder ser convertido para int ou float no
        tipo lógico esperado.
    """
    if _is_blank(raw_value):
        return np.nan

    logical_type = field_meta.get('logical_type', 'str')
    implicit_scale = field_meta.get('implicit_scale', None)
    token = str(raw_value).strip()

    if logical_type == 'int':
        try:
            return int(token)
        except ValueError:
            raise ValueError(f"Não foi possível converter '{token}' para int.")

    if logical_type == 'float':
        try:
            if '.' in token:
                return float(token)
            if implicit_scale is None:
                return float(token)
            return float(token) / (10 ** int(implicit_scale))
        except ValueError:
            raise ValueError(f"Não foi possível converter '{token}' para float.")

    return token


def _apply_field_parsing(df, cod_exec, fwf_specs):
    """Aplica parsing e coerção de tipos para todas as colunas do DataFrame.

    Args:
        df: DataFrame bruto produzido por pandas.read_fwf.
        cod_exec: Código de execução do bloco (ex.: DBAR, DLIN).
        fwf_specs: Layouts carregados via _load_runtime_specs.

    Returns:
        O próprio DataFrame com colunas convertidas conforme field_meta.

    Raises:
        ValueError: Se campo marcado como obrigatório contiver valor vazio ou
        inválido após coerção.
    """
    field_meta = fwf_specs.get(cod_exec, {}).get('field_meta', {})
    if not field_meta or df.empty:
        return df

    for col in df.columns:
        meta = field_meta.get(col, {})
        df[col] = df[col].apply(lambda v: _parse_field_value(v, meta))
        df[col] = _coerce_series_to_schema(df[col], meta)

        if meta.get('nullable', True) is False and df[col].isna().any():
            raise ValueError(f"Campo obrigatório '{col}' em {cod_exec} contém valor vazio/inválido.")

    return df


def _reparse_token(token, field_meta):
    """Reinterpreta um token textual com a mesma semântica do leitor.

    Args:
        token: Texto candidato à escrita no campo.
        field_meta: Metadados do campo (usa implicit_scale).

    Returns:
        Valor float equivalente ao que _parse_field_value devolveria para o
        token, ou None quando o token não é numérico.
    """
    implicit_scale = field_meta.get('implicit_scale', None)
    text = str(token).strip()
    if text == '':
        return None
    try:
        if '.' in text:
            return float(text)
        if implicit_scale is None:
            return float(text)
        return float(text) / (10 ** int(implicit_scale))
    except (TypeError, ValueError):
        return None


def _float_candidates(numeric, field_meta):
    """Gera representações textuais candidatas para um float.

    Args:
        numeric: Valor float a representar.
        field_meta: Metadados do campo (implicit_scale, preserve_trailing_dot,
            allow_scientific).

    Returns:
        Lista de strings candidatas, sem filtro de largura.
    """
    implicit_scale = field_meta.get('implicit_scale', None)
    preserve_trailing_dot = field_meta.get('preserve_trailing_dot', False)
    allow_scientific = field_meta.get('allow_scientific', True)

    candidates = []

    def _add(text):
        if text and text not in candidates:
            candidates.append(text)

    # 1) Ponto fixo, de mais para menos casas decimais.
    for decimals in range(10, -1, -1):
        fixed = f'{numeric:.{decimals}f}'
        if fixed.startswith('-0') and float(fixed) == 0.0:
            fixed = fixed[1:]
        _add(fixed)

        # Sem zeros a direita (e, opcionalmente, sem o ponto final).
        if '.' in fixed:
            trimmed = fixed.rstrip('0')
            _add(trimmed)                  # mantem o ponto final (ex.: '1.')
            if not preserve_trailing_dot:
                _add(trimmed.rstrip('.'))  # forma enxuta (ex.: '1')

    # 2) Compact: sem o zero a esquerda quando 0 < |x| < 1.
    for base in list(candidates):
        if base.startswith('0.'):
            _add(base[1:])
        elif base.startswith('-0.'):
            _add('-' + base[2:])

    # 3) Ponto decimal implicito: forma sem ponto (o leitor divide por 10**s).
    if implicit_scale is not None:
        scaled = numeric * (10 ** int(implicit_scale))
        if abs(scaled - round(scaled)) <= max(1e-9, abs(scaled) * 1e-9):
            _add(str(int(round(scaled))))

    # 4) Notacao cientifica compacta (ex.: 1.2E-4).
    if allow_scientific and numeric != 0.0:
        for mantissa_digits in range(6, -1, -1):
            sci = f'{numeric:.{mantissa_digits}E}'
            mantissa, _, expoente = sci.partition('E')
            if '.' in mantissa:
                mantissa = mantissa.rstrip('0').rstrip('.')
            sinal = '-' if expoente.startswith('-') else ''
            expoente_num = expoente.lstrip('+-').lstrip('0') or '0'
            _add(f'{mantissa}E{sinal}{expoente_num}')

    return candidates


def _serialize_float_value(numeric, field_meta, field_width=None,
                           cod_exec=None, field_name=None, row_index=None):
    """Escolhe a melhor representação textual de um float para o campo.

    Gera candidatos (ponto fixo, compact, ponto decimal implícito e notação
    científica), descarta os que não cabem na largura e devolve o de menor erro
    de round-trip. Empates são resolvidos preferindo o ponto final explícito em
    valores inteiros, depois o texto mais curto e, por fim, a forma não
    científica.

    Args:
        numeric: Valor float a serializar.
        field_meta: Metadados do campo.
        field_width: Largura máxima do campo (None = sem limite).
        cod_exec: Código de execução, apenas para mensagens de erro.
        field_name: Nome do campo, apenas para mensagens de erro.
        row_index: Índice da linha, apenas para mensagens de erro.

    Returns:
        String serializada que cabe na largura do campo.

    Raises:
        ValueError: Quando nenhum candidato representa o valor sem zerá-lo
            dentro da largura disponível.
    """
    melhor = None
    eh_inteiro = abs(numeric - round(numeric)) <= 1e-9
    for candidate in _float_candidates(numeric, field_meta):
        if field_width is not None and len(candidate) > field_width:
            continue
        reparsed = _reparse_token(candidate, field_meta)
        if reparsed is None:
            continue
        if numeric != 0.0 and reparsed == 0.0:
            continue  # nunca zerar um valor nao nulo
        erro = abs(reparsed - numeric)
        # Para valores inteiros, a convencao do ANAREDE e o ponto final
        # explicito ('2610.'); a forma nua so e usada se for a unica que cabe.
        inteiro_sem_ponto = (
            eh_inteiro and '.' not in candidate and 'E' not in candidate.upper()
        )
        chave = (erro, int(inteiro_sem_ponto), len(candidate), 'E' in candidate.upper())
        if melhor is None or chave < melhor[0]:
            melhor = (chave, candidate)

    if melhor is None:
        raise ValueError(
            f"Nao foi possivel representar o valor {numeric!r} no campo "
            f"{cod_exec or '?'}.{field_name or '?'} (linha {row_index}, "
            f"largura {field_width}) sem zera-lo ou trunca-lo."
        )
    return melhor[1]


def _serialize_field_value(value, field_meta, field_width=None,
                           cod_exec=None, field_name=None, row_index=None):
    """Serializa valor lógico para a representação textual esperada no PWF.

    Args:
        value: Valor lógico (numérico, texto ou nulo).
        field_meta: Metadados do campo (tipo, escala e formatação).
        field_width: Largura máxima do campo no layout de saída.
        cod_exec: Código de execução, usado nas mensagens de erro.
        field_name: Nome do campo, usado nas mensagens de erro.
        row_index: Índice da linha, usado nas mensagens de erro.

    Returns:
        String serializada para escrita em largura fixa.

    Raises:
        ValueError: Quando um valor float não nulo não pode ser representado na
            largura do campo sem ser zerado ou truncado.
    """
    if _is_blank(value):
        return ''

    logical_type = field_meta.get('logical_type', 'str')

    if logical_type == 'int':
        try:
            return str(int(float(value)))
        except (TypeError, ValueError):
            return str(value).strip()

    if logical_type == 'float':
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return str(value).strip()
        return _serialize_float_value(numeric, field_meta, field_width,
                                      cod_exec=cod_exec, field_name=field_name,
                                      row_index=row_index)

    return str(value).strip()


def _dataframe_to_serializable_rows(df, cod_exec, fwf_specs, field_lengths):
    """Converte DataFrame em linhas serializáveis para escrita do bloco.

    Args:
        df: DataFrame de um código de execução.
        cod_exec: Código de execução correspondente ao layout em fwf_specs.
        fwf_specs: Layouts carregados via _load_runtime_specs.
        field_lengths: Larguras de campo por cod_exec, de _load_runtime_specs.

    Returns:
        Lista de linhas, em que cada linha é uma lista de strings já
        serializadas por campo.
    """
    rows = []
    field_names = fwf_specs[cod_exec]['names']
    field_meta = fwf_specs[cod_exec].get('field_meta', {})

    for row_index, (_, row) in enumerate(df.iterrows()):
        serialized = []
        for idx, field_name in enumerate(field_names):
            meta = field_meta.get(field_name, {})
            serialized.append(_serialize_field_value(
                row[field_name], meta, field_lengths[cod_exec][idx],
                cod_exec=cod_exec, field_name=field_name, row_index=row_index,
            ))
        rows.append(serialized)
    return rows


def _is_multiline_exec(cod_exec, block_rules):
    """Indica se o bloco usa formato de múltiplas linhas por registro lógico.

    Args:
        cod_exec: Código de execução do bloco PWF.
        block_rules: Regras de bloco carregadas via _load_runtime_specs.

    Returns:
        True quando block_rules define separador de multiline e quantidade de
        campos de cabeçalho; caso contrário, False.
    """
    cfg = block_rules.get(cod_exec, {})
    return bool(cfg.get('multiline_separator')) and cfg.get('header_field_count') is not None


def _format_segment(line_values, cod_exec, start_idx, end_idx, field_lengths, field_gaps,
                    field_names=None, row_index=None):
    """Formata um trecho de campos em largura fixa para escrita no arquivo.

    Args:
        line_values: Lista de valores já serializados em string.
        cod_exec: Código de execução para consultar largura e gaps.
        start_idx: Índice inicial (inclusivo) dos campos a escrever.
        end_idx: Índice final (exclusivo) dos campos a escrever.
        field_lengths: Larguras de campo por cod_exec, de _load_runtime_specs.
        field_gaps: Espaços entre campos por cod_exec, de _load_runtime_specs.
        field_names: Nomes dos campos, usados nas mensagens de erro.
        row_index: Índice da linha, usado nas mensagens de erro.

    Returns:
        String com o segmento formatado no padrão do layout.

    Raises:
        ValueError: Quando o texto serializado não cabe na largura do campo
            (nunca há truncamento silencioso).
    """
    string = ''
    for j in range(start_idx, end_idx):
        dat = line_values[j]
        largura = field_lengths[cod_exec][j]
        if len(dat) > largura:
            nome = field_names[j] if field_names is not None and j < len(field_names) else f'#{j}'
            raise ValueError(
                f"Valor '{dat}' nao cabe no campo {cod_exec}.{nome} "
                f"(linha {row_index}, largura {largura})."
            )
        string += '{dat:>{wid}}'.format(dat=dat, wid=largura) + field_gaps[cod_exec][j]
    return string



def carrega_pwf(path, json_path=None):
    """Lê um arquivo .PWF e retorna os blocos reconhecidos em DataFrames.

    Args:
        path: Caminho para o arquivo .PWF de entrada.
        json_path: Caminho para o JSON de specs (uma versão do ANAREDE); usa
            pwf_specs_v12.json ao lado deste módulo quando None.

    Returns:
        Dicionário indexado por código de execução. Para cada bloco lido com
        sucesso, o valor é um pandas DataFrame tipado conforme o schema. Para
        blocos ausentes ou sem terminador no schema, o valor permanece lista vazia.

    Raises:
        ValueError: Se um bloco reconhecido falhar no parsing ou não tiver seu
            terminador obrigatório. A mensagem informa arquivo, bloco e linhas.

    Notes:
        - Blocos multiline (como DAGR e DBSH) usam regras do schema.
        - Blocos sem terminador no schema (como TITU) não são lidos.
        - Linhas vazias e comentários iniciados por '(' são ignorados.
        - Erros interrompem a leitura; não há retorno parcial de blocos.
    """
    specs = _load_runtime_specs(json_path)
    fwf_specs, cod_exec_list, block_rules = specs['fwf_specs'], specs['cod_exec'], specs['block_rules']

    # Ordem dos blocos no dicionario sera a ordem de escrita: usa a ordem
    # canonica para os codigos conhecidos e acrescenta ao final os codigos que
    # existam no schema mas nao estejam na lista canonica.
    ordem = [cod for cod in BLOCK_WRITE_ORDER if cod in cod_exec_list]
    ordem += [cod for cod in cod_exec_list if cod not in ordem]
    pwf_dict: dict[str, pd.DataFrame | list] = {cod: [] for cod in ordem}

    exec_atual = None
    buffer = []
    inicio_bloco = 0
    numero_linha = 0
    nfr_flag = True
    nfr_header = ''

    with open(path, mode='r', encoding="latin-1") as file:
        for numero_linha, line in enumerate(file, start=1):
            line = line.rstrip('\r\n')
            if not line.strip() or line.startswith('('):
                continue

            novo_bloco = line[:4] in cod_exec_list
            if novo_bloco or line == 'FIM':
                if exec_atual is not None:
                    raise ValueError(
                        f"Arquivo {path}, bloco {exec_atual}, linhas "
                        f"{inicio_bloco}-{numero_linha}: terminador 99999 ausente."
                    )
                if line == 'FIM':
                    break
                codigo = line[:4]
                if not block_rules[codigo]['has_terminator']:
                    continue
                exec_atual = codigo
                inicio_bloco = numero_linha
                buffer = []
                nfr_flag = True
                continue

            if exec_atual is None:
                continue

            if line == '99999':
                try:
                    with io.StringIO('\n'.join(buffer)) as stream:
                        dataframe = pd.read_fwf(
                            stream,
                            colspecs=fwf_specs[exec_atual]['colspecs'],
                            names=fwf_specs[exec_atual]['names'],
                            dtype=str,
                        )
                    dataframe = _apply_field_parsing(dataframe, exec_atual, fwf_specs)
                except Exception as exc:
                    raise ValueError(
                        f"Arquivo {path}, bloco {exec_atual}, linhas "
                        f"{inicio_bloco}-{numero_linha}: {exc}"
                    ) from exc
                pwf_dict[exec_atual] = dataframe
                exec_atual = None
                continue

            if _is_multiline_exec(exec_atual, block_rules):
                separator = block_rules[exec_atual]['multiline_separator']
                if line.startswith(separator):
                    nfr_flag = True
                    continue
                if nfr_flag:
                    nfr_header = line
                    nfr_flag = False
                    continue
                buffer.append(nfr_header + line)
            else:
                buffer.append(line)

    if exec_atual is not None:
        raise ValueError(
            f"Arquivo {path}, bloco {exec_atual}, linhas "
            f"{inicio_bloco}-{numero_linha}: terminador 99999 ausente no fim do arquivo."
        )

    return pwf_dict

def escreve_pwf(pwf_dict, *CODEXECS, pwf_path='output.PWF', json_path=None):
    """Escreve um arquivo .PWF a partir de DataFrames por código de execução.

    Args:
        pwf_dict: Dicionário cod_exec -> DataFrame ou, alternativamente, um
            único DataFrame quando CODEXECS for informado.
        *CODEXECS: Código de execução associado ao DataFrame único recebido em
            pwf_dict.
        pwf_path: Caminho de saída do arquivo .PWF.
        json_path: Caminho para o JSON de specs (uma versão do ANAREDE); usa
            pwf_specs_v12.json ao lado deste módulo quando None.

    Returns:
        None.

    Raises:
        Exception: Quando pwf_dict é DataFrame e CODEXECS não é informado.
        ValueError: Quando algum valor não pode ser representado na largura do
            campo (nunca há truncamento silencioso).

    Notes:
        - Os DataFrames recebidos não são modificados: DBSH e DAGR são
          ordenados em cópia local antes da escrita.
        - Blocos sem terminador no schema (ex.: TITU) não são escritos.
        - Para blocos multiline (ex.: DBSH e DAGR), o writer emite linhas de
          cabeçalho e linhas complementares separadas por marcadores (FBAN,
          FAGR), conforme as regras do schema.
        - O arquivo é sobrescrito e finalizado com a linha FIM.
    """
    specs = _load_runtime_specs(json_path)
    fwf_specs, block_rules = specs['fwf_specs'], specs['block_rules']
    field_lengths, field_gaps = specs['field_lengths'], specs['field_gaps']

    COD_EXEC_exception = tuple(
        cod for cod, cfg in block_rules.items()
        if not cfg.get('has_terminator', True)
    )
    

    ## checa se foi passado um dict com vários cod_execs ou só um df com um cod_exec apenas
    if len(CODEXECS) and isinstance(pwf_dict, pd.DataFrame):
        aux = pwf_dict
        pwf_dict = dict([(CODEXECS[0], aux)]) 
    elif not len(CODEXECS) and isinstance(pwf_dict, pd.DataFrame):
        raise Exception('Foi passado um argumento tipo Dataframe, mas não ' 
                        'foi informado a qual Código de Execução ele se ' 
                        'refere.')
    
    ## Escrita do arquivo propriamente dita
    with open(pwf_path, 'w', encoding='latin-1') as file:  
        pass

    for cod_exec in pwf_dict:
        dataframe = pwf_dict[cod_exec]
        if not isinstance(dataframe, pd.DataFrame):
            continue  # codigos de execucao no pwf_dict que nao sao dataframe

        # organiza o DBSH e DAGR para impressao (em copia, sem mexer no caller)
        if cod_exec == 'DBSH' and {'De', 'Para', 'Nc'}.issubset(dataframe.columns):
            dataframe = dataframe.sort_values(by=['De', 'Para', 'Nc'])    ## classifica pelo id
        elif cod_exec == 'DAGR' and 'AG' in dataframe.columns:
            dataframe = dataframe.sort_values(by=['AG'])    ## classifica pelo id
        else:
            dataframe = dataframe.copy()

        if cod_exec in fwf_specs:
            dataframe = _preenche_colunas_faltantes(dataframe, fwf_specs[cod_exec]['names'])
        if cod_exec not in COD_EXEC_exception:
            field_names = fwf_specs[cod_exec]['names']
            pwf_list = _dataframe_to_serializable_rows(dataframe, cod_exec, fwf_specs, field_lengths)

            ## aplica o fixedwidth para cada campo
            fwt = []
            if _is_multiline_exec(cod_exec, block_rules):
                separator = block_rules[cod_exec]['multiline_separator']
                header_count = block_rules[cod_exec]['header_field_count']
                memo = None
                for row_index, line in enumerate(pwf_list):
                    header_values = line[:header_count]
                    if memo is not None and memo != header_values:
                        fwt.append(separator)
                    if memo is None or memo != header_values:
                        fwt.append(_format_segment(line, cod_exec, 0, header_count, field_lengths,
                                                   field_gaps, field_names, row_index))
                        memo = header_values
                    fwt.append(_format_segment(line, cod_exec, header_count, len(line), field_lengths,
                                               field_gaps, field_names, row_index))
            else:
                for row_index, line in enumerate(pwf_list):
                    fwt.append(_format_segment(line, cod_exec, 0, len(line), field_lengths,
                                               field_gaps, field_names, row_index))

            ## escreve o arquivo
            with open(pwf_path, 'a+', encoding='latin-1') as file:
                header_suffix = block_rules.get(cod_exec, {}).get('header_suffix', '')
                file.write(cod_exec + header_suffix + '\n')
                for line in fwt:
                    file.write(''.join(line) + '\n')
                if (cod_exec not in COD_EXEC_exception):
                    file.write('99999\n')

    with open(pwf_path, 'a', encoding='latin-1') as file:
        file.write('FIM\n')


# =====================================================================================
# ----- MAIN --------

if __name__ == '__main__':
    # Exemplo minimo de uso: le um deck de testes e reescreve em testes/saida.
    RAIZ = Path(__file__).resolve().parents[2]
    ENTRADA = RAIZ / 'testes' / 'entrada' / '2036_PDE35.PWF'
    SAIDA = RAIZ / 'testes' / 'saida' / '2036_PDE35_OUT.PWF'
    SPECS = Path(__file__).resolve().with_name(DEFAULT_SPECS_FILENAME)

    dados = carrega_pwf(ENTRADA, json_path=SPECS)
    print({cod: len(df) for cod, df in dados.items() if isinstance(df, pd.DataFrame)})

    SAIDA.parent.mkdir(parents=True, exist_ok=True)
    escreve_pwf(dados, pwf_path=str(SAIDA), json_path=SPECS)
    print(f'Arquivo gerado em {SAIDA}')
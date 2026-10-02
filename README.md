# pwftoolbox

Pacote Python para **leitura e escrita de arquivos `.PWF` do ANAREDE** (CEPEL). Cada bloco (código de execução) do deck é convertido em um `pandas.DataFrame` e o caminho inverso escreve o arquivo serializando em largura fixa.

O layout de cada bloco é definido em um **schema JSON externo** (`pwf_specs_v12.json`, `pwf_specs_v13.json`), um por versão do ANAREDE.

> **Em desenvolvimento.** Projeto pode atualizar os blocos lidos e aprimorar a escrita dos arquivos. 

## Instalação

- Python >= 3.10
- `pandas` e `numpy` (instalados automaticamente como dependências)

Com o repositório clonado, execute na sua raiz:

```bash
python -m pip install .
```

Para desenvolvimento, use a instalação editável:

```bash
python -m pip install -e .
```

Também é possível instalar diretamente do repositório GitHub:

```bash
python -m pip install git+https://github.com/EPE-GOV-BR/pwftoolbox.git
```



## Como usar

```python
from pwftoolbox import carrega_pwf, escreve_pwf

# Leitura: devolve dict {codigo de execucao: DataFrame}
dados = carrega_pwf("caso.PWF")

dados["DBAR"]["Pl"] *= 1.1          # ex.: +10% de carga ativa em todas as barras
dados["DLIN"]["Tap"] = 1.0

# Escrita: regrava o dicionario inteiro
escreve_pwf(dados, pwf_path="caso_out.PWF")

# Ou apenas um bloco, informando o codigo de execucao
escreve_pwf(dados["DBAR"], "DBAR", pwf_path="dbar.PWF")
```

Pontos importantes:

- `carrega_pwf` devolve **todos** os códigos do schema: blocos ausentes no arquivo ficam como lista vazia (`[]`); blocos lidos viram `DataFrame` com tipos nuláveis (`Int64`, `Float64`, `string`).
- A **ordem das chaves do dicionário é a ordem de escrita** do arquivo de saída.
- A **versão do ANAREDE é escolhida pelo `json_path`**. Sem esse argumento, usa-se o
  `pwf_specs_v12.json` ao lado do módulo.
- Campos vazios (`NaN`/`<NA>`) são escritos em branco. Textos maiores que a largura do campo levantam `ValueError`, em vez de serem cortados. Números podem ser arredondados para caber; consulte as limitações de precisão abaixo.
- Falhas de parsing interrompem a leitura com `ValueError`, informando arquivo, bloco e faixa de linhas. Blocos malformados não são descartados silenciosamente.
- Colunas que não existirem no DataFrame são criadas vazias na escrita, então é possível montar um bloco só com os campos identificadores (ver `testes/deck_manipulado.py`).

## Códigos de execução suportados

Os schemas contêm 25 códigos, incluindo `TITU`, que não é lido nem escrito. Os outros 24 são:

| Código | Conteúdo | Observações |
| ------ | -------- | ----------- |
| `DOPC` | Opções de controle de execução | |
| `DCTE` | Constantes do estudo | |
| `DAGR` | Agregadores e suas ocorrências | **Multiline**: linhas complementares separadas por `FAGR` |
| `DBAR` | Dados de barra | |
| `DGER` | Limites de geração por barra | |
| `DLIN` | Dados de circuito CA (linhas e transformadores) | |
| `DCSC` | Compensador série controlável | |
| `DBSH` | Bancos de capacitores/reatores manobráveis | **Multiline**: linhas complementares separadas por `FBAN` |
| `DSHL` | Shunt de circuito | |
| `DCER` | Compensador estático de reativo | |
| `DCTR` | Dados complementares de transformador | |
| `DGLT` | Grupos de limite de tensão | |
| `DARE` | Áreas | |
| `DTPF` | Fixação no controle de tensão por transformador | Cabeçalho escrito como `DTPF CIRC` (só a opção `CIRC` é suportada) |
| `DELO` | Elos CC | |
| `DCBA` | Barras CC | |
| `DCLI` | Linhas CC | |
| `DCNV` | Conversores CC | |
| `DCCV` | Controle de conversores CC | |
| `DVSC` | Conversores VSC | Modelo **simplificado** na v12 e **completo** na v13 (campos diferentes) |
| `DGBT` | Grupos de base de tensão | |
| `DCMT` | Comentários | |
| `DCAI` | Cargas individualizadas | |
| `DGEI` | Geradores individualizados | |


## Versões de `.PWF` suportadas

| Arquivo | Versão do ANAREDE |
| ------- | ----------------- |
| `pwf_specs_v12.json` | 12.3.4 (padrão quando `json_path=None`) |
| `pwf_specs_v13.json` | 13.1.2 |

Use sempre o schema correspondente à versão do deck: as diferenças entre versões estão apenas nos JSONs. **Adicionar uma versão nova = criar um novo JSON de schema**, sem alterar o parser.

Para usar o schema v13 incluído no pacote, independentemente do diretório atual:

```python
from importlib.resources import as_file, files
from pwftoolbox import carrega_pwf, escreve_pwf

recurso = files("pwftoolbox").joinpath("pwf_specs_v13.json")
with as_file(recurso) as specs:
  dados = carrega_pwf("caso_v13.PWF", json_path=specs)
  escreve_pwf(dados, pwf_path="caso_v13_out.PWF", json_path=specs)
```

Também é possível fornecer um caminho para um schema próprio.

Cada campo do JSON declara:

| Chave | Significado |
| ----- | ----------- |
| `colspec` | `start`/`end` da coluna no registro de largura fixa (fim exclusivo). 0-based. |
| `type` | `str`, `int` ou `float` |
| `required` | Se `true`, valor vazio no bloco levanta erro na leitura |
| `default` | Valor padrão do ANAREDE (documental) |
| `implicit_scale` | Ponto decimal implícito: token sem ponto é lido como `valor / 10**implicit_scale` (ex.: `975` com escala 3 é `0.975`) |
| `preserve_trailing_dot` | Mantém a forma `N.` para valores inteiros do campo |
| `allow_scientific` | Permite (padrão) ou proíbe notação científica na escrita do campo |

## Estrutura do repositório

```text
pyproject.toml         # empacotamento, dependências e metadados
src/
  pwftoolbox/
    __init__.py       # API pública
    parser_pwf.py     # carrega_pwf / escreve_pwf
    pwf_specs_v12.json # schema do ANAREDE v12.3.4
    pwf_specs_v13.json # schema do ANAREDE v13.1.2
testes/
  entrada/             # arquivo de referencia (v12)
  saida/               # arquivos gerados pelos testes
  round_trip_pwf.py    # validação manual com relatório e log
  round_trip_automatizado.py # verificação automática v12 e casos sintéticos v13
  ler_escreve.py       # exemplo: le e reescreve um deck
  deck_manipulado.py   # exemplo: monta um deck reduzido com DBAR/DLIN alterados
```

## Limitações conhecidas

- **`TITU` não é lido nem reescrito.** Blocos sem terminador no schema são ignorados pelo leitor e não são emitidos pelo escritor; seu valor no dicionário permanece `[]`.
- **Comentários são descartados.** Linhas iniciadas por `(` (cabeçalhos de coluna e anotações) são ignoradas na leitura e não são reproduzidas na escrita.
- **Erros de leitura são explícitos.** Falhas de conversão ou campos obrigatórios vazios levantam `ValueError` com contexto e causa original, sem retornar dados parciais. A ausência de `99999` em um bloco com terminador obrigatório também levanta erro ao encontrar outro cabeçalho reconhecido, `FIM` ou o fim do arquivo. Linhas vazias são ignoradas e `FIM` encerra a leitura. Erros de abertura de arquivos e de schema também se propagam ao chamador.
- **O leitor não é um validador completo do ANAREDE.** Códigos desconhecidos e opções de bloco não suportadas não têm interpretação garantida. Blocos repetidos substituem os anteriores no dicionário, em vez de preservar a sequência de comandos do arquivo.
- **O texto gerado pode diferir do arquivo original, ainda que o valor seja o mesmo.** O escritor gera candidatos de formatação (ponto fixo, forma compacta sem o zero à esquerda, forma sem ponto para campos com `implicit_scale` e notação científica) e escolhe o de menor erro de round-trip que caiba na largura do campo. Ex.: `4.11` em campo de escala 2 pode sair como `411`, e `270000` em campo de 5 colunas sai como `2.7E5`.
- **Pode haver perda de precisão numérica sem aviso.** O escritor escolhe o candidato de menor erro que caiba, mas não exige erro zero nem impõe uma tolerância máxima. Floats podem perder casas decimais por arredondamento; por exemplo, `1.23456` em quatro colunas pode sair como `1.23`. Verifique os dados regravados com tolerância adequada ao estudo.
- **Nem todo número cabe no campo.** Floats não nulos que só poderiam ser escritos como zero e valores sem candidato disponível levantam `ValueError`. Se um campo não aceitar notação científica no ANAREDE, marque `"allow_scientific": false` no JSON.
- **A escrita não é atômica.** O destino é sobrescrito antes da validação completa; uma falha pode deixar um arquivo vazio ou parcial.
- Os JSONs cobrem `DTPF` apenas na opção `CIRC` e `DVSC` em um modelo por versão (simplificado na v12, completo na v13).

## Como contribuir

Praticamente tudo é feito no JSON de schema, não no parser:

- **Novo código de execução**: acrescente uma chave em `cod_exec` no JSON da versão, com `fields` (cada campo com `colspec`, `type` e, quando aplicável, `required`, `default`, `implicit_scale`, `preserve_trailing_dot`, `allow_scientific`) e as regras de bloco (`has_terminator`, `header_suffix`, `multiline_separator`, `header_field_count`). Inclua o código em `BLOCK_WRITE_ORDER` (`parser_pwf.py`) na posição em que ele deve ser escrito.
- **Nova versão do ANAREDE**: copie o JSON mais próximo, ajuste os blocos que mudaram e passe o novo caminho em `json_path`. 


## Autores

- Samir Ferreira
- Lucas Simões
- Miguel Sampaio

## Licença

GPL-3.0 — veja [LICENSE](LICENSE)

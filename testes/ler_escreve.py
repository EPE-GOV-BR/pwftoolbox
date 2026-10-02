# -*- coding: utf-8 -*-
"""
Le o deck 2036_PDE35.PWF e reescreve o arquivo sem alterar os dados.

Exemplo mais simples de uso do parser: carrega_pwf devolve o dicionario
codigo de execucao -> DataFrame e escreve_pwf grava o mesmo conteudo de volta
em .PWF (util para verificar leitura/escrita de um deck).

Uso:
    python testes/ler_escreve.py
"""

from __future__ import annotations

from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
JSON_SPECS = ROOT_DIR / "src" / "pwftoolbox" / "pwf_specs_v12.json"
INPUT_PWF = ROOT_DIR / "testes" / "entrada" / "2036_PDE35.PWF"
OUTPUT_PWF = ROOT_DIR / "testes" / "saida" / "2036_PDE35_OUT.PWF"

from pwftoolbox import parser_pwf


def main():
    pwf_dict = parser_pwf.carrega_pwf(INPUT_PWF, json_path=JSON_SPECS)
    print(f"Arquivo {INPUT_PWF} carregado com sucesso.")

    OUTPUT_PWF.parent.mkdir(parents=True, exist_ok=True)
    parser_pwf.escreve_pwf(pwf_dict, pwf_path=str(OUTPUT_PWF), json_path=JSON_SPECS)
    print(f"Arquivo gerado em {OUTPUT_PWF}")


if __name__ == "__main__":
    main()
# -*- coding: utf-8 -*-
"""
Gera um .PWF reduzido a partir de 2036_PDE35.PWF contendo apenas DBAR e DLIN.

Mantem somente os campos identificadores de cada bloco (Num para DBAR;
De, Para, Nc para DLIN), altera o campo O (Operacao) para 'M' em ambos os
blocos, multiplica Pl (Carga Ativa) por 1.1 no DBAR e fixa Tap em 1.0 no DLIN.

Uso:
    python testes/deck_manipulado.py
"""

from __future__ import annotations

from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
JSON_SPECS = ROOT_DIR / "src" / "pwftoolbox" / "pwf_specs_v12.json"
INPUT_PWF = ROOT_DIR / "testes" / "entrada" / "2036_PDE35.PWF"
OUTPUT_PWF = ROOT_DIR / "testes" / "saida" / "DECK_2036_PDE35.PWF"

from pwftoolbox import parser_pwf


def main():
    pwf_dict = parser_pwf.carrega_pwf(INPUT_PWF, json_path=JSON_SPECS)

    dbar = pwf_dict['DBAR'][['Num', 'Pl']].copy()
    dbar['O'] = 'M'
    dbar['Pl'] = dbar['Pl'] * 1.1

    dlin = pwf_dict['DLIN'][['De', 'Para', 'Nc']].copy()
    dlin['O'] = 'M'
    dlin['Tap'] = 1.0

    OUTPUT_PWF.parent.mkdir(parents=True, exist_ok=True)
    parser_pwf.escreve_pwf({'DBAR': dbar, 'DLIN': dlin}, pwf_path=str(OUTPUT_PWF), json_path=JSON_SPECS)

    print(f"Arquivo gerado em {OUTPUT_PWF}")


if __name__ == "__main__":
    main()

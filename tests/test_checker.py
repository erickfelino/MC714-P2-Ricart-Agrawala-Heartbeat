"""Testes do verificador com os logs de exemplo de tests/fixtures/.

Para cada caso, define quais checagens devem falhar. Os casos "ok_*" não
podem ter nenhuma falha; os casos "erro_*" precisam falhar exatamente na
propriedade que foi violada de propósito.

Rodar (na raiz do repositório):
    python -m unittest discover -s tests -v
"""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from checker.core import check_run  # noqa: E402

FIXTURES = os.path.join(ROOT, "tests", "fixtures")

ME = "Exclusão mútua"
COUNTER = "Contador correto"
LIVE = "Vivacidade"
FALSE_S = "Sem suspeitas falsas"
COMPLETE = "Completude"
DETECT = "Tempo de detecção"

# caso -> conjunto de checagens que DEVEM falhar
EXPECTED_FAILURES = {
    "ok_normal": set(),
    "ok_kill_fora_da_rc": set(),
    "ok_kill_dentro_da_rc": set(),
    "erro_sobreposicao": {ME},
    "erro_atualizacao_perdida": {COUNTER},
    "erro_pedido_nao_atendido": {LIVE},
    "erro_suspeita_falsa": {FALSE_S},
    "erro_suspeita_antes_do_kill": {FALSE_S, DETECT},
    "erro_deteccao_incompleta": {COMPLETE},
    "erro_deteccao_lenta": {DETECT},
    "erro_entrou_com_morto_na_rc": {ME},
    "erro_no_nao_terminou": {LIVE},
}


class TestChecker(unittest.TestCase):
    def test_all_fixtures_have_expectations(self):
        found = {d for d in os.listdir(FIXTURES) if os.path.isdir(os.path.join(FIXTURES, d))}
        self.assertEqual(found, set(EXPECTED_FAILURES), "caso sem expectativa ou expectativa sem caso")

    def test_fixtures(self):
        for case, expected in EXPECTED_FAILURES.items():
            with self.subTest(case=case):
                run, results, passed = check_run(os.path.join(FIXTURES, case))
                failed = {r.name for r in results if not r.passed}
                self.assertEqual(failed, expected, f"{case}: falhou em {failed}, esperado {expected}")
                self.assertEqual(passed, not expected)

    def test_truncated_line_is_tolerated(self):
        run, results, passed = check_run(os.path.join(FIXTURES, "ok_kill_fora_da_rc"))
        self.assertTrue(any("cortada" in w for w in run.warnings))
        self.assertTrue(passed)


if __name__ == "__main__":
    unittest.main()

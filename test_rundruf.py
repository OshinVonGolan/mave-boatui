"""Wie oft die Oberflaeche einen neuen Zustand bekommt.

Gemessen am 04.09.2026: 318 CAN-Rahmen je Sekunde. Mit dem alten Fenster von
50 ms hiess das 20 Rundrufe je Sekunde — jeder kostet den Pi 2,74 ms (Zustand
einsammeln, 17 Alarmregeln pruefen, JSON schreiben), und am anzeigenden Geraet
kam derselbe Takt noch einmal an.

Der Takt liegt jetzt bei 200 ms. Die Ausnahme ist der Fall, in dem es auf
Millisekunden ankommt: wer eben auf einen Knopf gedrueckt hat, wartet auf genau
diese eine Antwort.

Aufruf:

    ./venv/bin/python -m unittest test_rundruf -v
"""
import asyncio
import os
import time
import unittest

os.environ.setdefault('MAVE_PASSWORT', 'x' * 20)

from can_reader import BoatState, CanInterface  # noqa: E402


def bauen() -> CanInterface:
    """Eine Schnittstelle ohne Bus. Gesendet wird hier nichts, nur getaktet."""
    return CanInterface(channel='vcan-test', state=BoatState())


class DasFenster(unittest.IsolatedAsyncioTestCase):
    """Welche Wartezeit gewaehlt wird — ohne auf die Uhr angewiesen zu sein."""

    async def _gewaehlte_pause(self, ci: CanInterface) -> float:
        gemessen = []
        echt = asyncio.sleep

        async def merken(s, *a, **k):
            gemessen.append(s)
            return await echt(0, *a, **k)

        ci.set_loop(asyncio.get_running_loop())
        ci.on_change(lambda d: asyncio.sleep(0))
        asyncio.sleep = merken
        try:
            ci._broadcast_pending = True
            await ci._delayed_broadcast()
        finally:
            asyncio.sleep = echt
        return gemessen[0]

    async def test_im_normalfall_200_ms(self):
        self.assertAlmostEqual(await self._gewaehlte_pause(bauen()), 0.20, places=3)

    async def test_nach_einem_befehl_50_ms(self):
        ci = bauen()
        ci.eilig()
        self.assertAlmostEqual(await self._gewaehlte_pause(ci), 0.05, places=3)

    async def test_das_eilfenster_laeuft_ab(self):
        ci = bauen()
        ci.eilig(0.0)                       # sofort abgelaufen
        self.assertAlmostEqual(await self._gewaehlte_pause(ci), 0.20, places=3)


class WerDasFensterOeffnet(unittest.TestCase):
    """Die Wege, auf denen ein Mensch etwas schaltet — und nur die."""

    def setUp(self):
        self.ci = bauen()
        self.assertEqual(self.ci._eilig_bis, 0.0)

    def test_licht(self):
        self.ci.send_brightness([0] * 9)
        self.assertGreater(self.ci._eilig_bis, time.monotonic())

    def test_wechselrichter(self):
        self.ci.send_inverter_mode(2)
        self.assertGreater(self.ci._eilig_bis, time.monotonic())

    def test_ladegeraet(self):
        self.ci.send_charger_register(0x0200, 1, size=1)
        self.assertGreater(self.ci._eilig_bis, time.monotonic())

    def test_uhrzeit_nicht(self):
        """Die Zeitsynchronisierung laeuft im Hintergrund. Niemand sieht ihr zu,
        also braucht sie auch keinen schnelleren Takt."""
        self.ci.send_time(time.time())
        self.assertEqual(self.ci._eilig_bis, 0.0)


class DerTaktInEchtzeit(unittest.IsolatedAsyncioTestCase):
    """Einmal wirklich mitzaehlen — die Rechnung oben nuetzt nichts, wenn das
    Entprellen selbst nicht mehr entprellt."""

    async def _zaehlen(self, ci: CanInterface, dauer_s: float) -> int:
        rufe = []

        async def merken(d):
            rufe.append(d)

        ci.set_loop(asyncio.get_running_loop())
        ci.on_change(merken)
        ende = time.monotonic() + dauer_s
        while time.monotonic() < ende:
            ci._schedule_broadcast()        # so, als kaeme laufend ein Rahmen
            await asyncio.sleep(0.005)
        await asyncio.sleep(0.3)            # den letzten noch abwarten
        return len(rufe)

    async def test_hoechstens_fuenf_je_sekunde(self):
        n = await self._zaehlen(bauen(), 1.0)
        # Grosszuegige Grenzen: auf einer belasteten Maschine wackelt der Takt.
        # Die Aussage ist "nicht mehr zwanzig", nicht "exakt fuenf".
        self.assertLessEqual(n, 9, f'{n} Rundrufe in einer Sekunde — zu viele')
        self.assertGreaterEqual(n, 3, f'nur {n} Rundrufe — das ist zu traege')

    async def test_im_eilfenster_deutlich_mehr(self):
        ci = bauen()
        ci.eilig(5.0)
        n = await self._zaehlen(ci, 1.0)
        self.assertGreaterEqual(n, 10, f'nur {n} Rundrufe im Eilfenster')


if __name__ == '__main__':
    unittest.main()

# MakingForGood mmWave Radar

Local fall-warning service for the TI IWR6843AOP (Radar Toolbox 4.30, 3D People Tracking firmware), Windows, Python 3.9.

- `radar_service/` – the working system: `Start-Radar.cmd` → `bootstrap.py` (pre-start checks) → `service.py` (serial reader, fall detector, local web page at http://127.0.0.1:8765/).
- Setup and usage (Chinese): [radar_service/README-ZH.md](radar_service/README-ZH.md)
- Fall settings: `radar_service/config.json` → `"fall"`. A fall alert needs a big, fast height drop that stays low outside `safe_zones`; slow lying down is logged as an info event only; staying low on the floor for `floor_alert_s` raises `prolonged_low`.

Tests:

    cd radar_service
    python -m unittest discover -s tests -v
    node tests/test_frontend.cjs
    node tests/test_frontend_kinds.cjs

Offline tests use synthetic tracks; the rule still needs validation on the real radar.

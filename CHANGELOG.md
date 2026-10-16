# Registro de cambios

## P1 — 2026-10-15 (Colombia)

Código nuevo: `app/models/contracts.py`, `app/core/state.py`, `app/core/state_manager.py`, pruebas en `tests/core/test_state.py` y utilidades. Documentos: arquitectura, decisiones, roadmap, test status y continuidad. Resultado: 17 pruebas aprobadas. Pendiente: flujo de agentes.

## P0 — 2026-10-15 (Colombia)

Base aislada y persistida; antecedentes documentados sin modificación. No había fuente histórica ni versión ejecutable confirmada.

## P2 — 2026-10-15 (Colombia)

Planner puro, Router con registro explícito y Orchestrator asíncrono acotado; HistoryAgent y ClinicalAgent simulados. Nuevas pruebas: 22 aprobadas, con fallos, cancelación, timeout y ocho casos concurrentes. Evidencia: `evidence/P2_tests.txt`. Riesgo: timeout cooperativo no detiene código no confiable; solo agentes locales de confianza. Siguiente: P3, contratos exportables, CLI y endurecimiento.

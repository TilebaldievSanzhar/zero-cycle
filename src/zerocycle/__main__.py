"""`python -m zerocycle …` — то же, что команда `zerocycle`.

На Windows со Smart App Control .exe-обёртку команды блокирует политика — тогда так.
"""

from zerocycle.cli import main

raise SystemExit(main())

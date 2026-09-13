"""Allow ``python -m client`` (instead of ``python -m client.main``).

Run from ``capturer/`` with the venv active.
"""

import asyncio

from client.main import main

if __name__ == "__main__":
    asyncio.run(main())
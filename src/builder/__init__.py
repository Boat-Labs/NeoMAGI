"""Builder work memory substrate (P2-M1c).

Provides structured work memory for builder tasks:
- BuilderTaskRecord: canonical artifact record type
- Artifact generation, rendering, and persistence (workspace/artifacts/)
- Work memory lifecycle (create / update / link to bead index)
"""

from __future__ import annotations

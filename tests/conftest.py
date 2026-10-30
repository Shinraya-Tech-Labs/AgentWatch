import datetime
import os
import tempfile

# v3 stores must never touch a developer's real ~/.agentwatch or ./data during tests
_AW3_TMP = tempfile.mkdtemp(prefix="panoptis-tests-")
os.environ["AGENTWATCH_HOME"] = _AW3_TMP
os.environ["AGENTWATCH_STORE"] = f"sqlite:///{_AW3_TMP}/agentwatch-v3.db".replace("\\", "/")

# after the environment above: importing panoptis must see the isolated test store
import panoptis.memory.engine  # noqa: E402
import panoptis.scoring.drift  # noqa: E402

if not hasattr(datetime, "UTC"):
    datetime.UTC = datetime.timezone.utc  # noqa: UP017

panoptis.scoring.drift._st_model = panoptis.scoring.drift._ST_UNAVAILABLE


async def mock_load(self):
    self._disabled = True


panoptis.memory.engine.EmbeddingProvider._load = mock_load

from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from qps.trading.advanced import LiveDeploymentFactory, LiveSafetyError


def test_paper_and_live_use_distinct_deployment_directories() -> None:
    with TemporaryDirectory() as directory:
        factory = LiveDeploymentFactory()
        paper = factory.create(deployment_id="paper", release_id="r1", account_id="a1", data_dir=str(Path(directory) / "paper"), mode="paper")
        live = factory.create(deployment_id="live", release_id="r1", account_id="a2", data_dir=str(Path(directory) / "live"), mode="live")
        assert paper["data_dir"] != live["data_dir"]
        with pytest.raises(LiveSafetyError):
            factory.create(deployment_id="other", release_id="r1", account_id="a3", data_dir=str(Path(directory) / "paper"), mode="live")

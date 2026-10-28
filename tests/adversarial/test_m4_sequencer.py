from concurrent.futures import ThreadPoolExecutor

from qps.trading.kernel import AccountSequencer


def test_account_sequencer_serializes_mutations() -> None:
    sequencer = AccountSequencer()
    versions: list[int] = []

    def mutate(version: int) -> int:
        versions.append(version)
        return version

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda _: sequencer.submit("paper", mutate), range(20)))
    assert sorted(versions) == list(range(20))


import os

import pytest
from typesafe_sdk import AsyncTypeSafeClient, Choice


@pytest.mark.live
@pytest.mark.asyncio
async def test_live_jev_with_synthetic_event() -> None:
    if os.getenv("RUN_LIVE_JEV_TESTS") != "1" or not os.getenv("TYPESAFE_API_KEY"):
        pytest.skip("set RUN_LIVE_JEV_TESTS=1 and TYPESAFE_API_KEY to run live Jev tests")

    async with AsyncTypeSafeClient() as client:
        response = await client.system_one(  # pyright: ignore[reportUnknownMemberType]
            state={
                "company": "Synthetic Test Company",
                "event": "The synthetic company reported a clearly labeled test order increase.",
            },
            questions={
                "direction": Choice(
                    instructions="What is the direction of `event` for `company`?",
                    criteria={"POSITIVE": None, "NEUTRAL": None, "NEGATIVE": None},
                )
            },
        )

    assert response.choices["direction"].choice in {"POSITIVE", "NEUTRAL", "NEGATIVE"}

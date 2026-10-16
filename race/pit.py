"""
Pit stop maths: time lost in a stop and where the player rejoins.
"""

# Approximate green-flag time lost by a stop (pit lane plus ~2.5 s stationary), seconds, by track id
PIT_LOSS_S = {
    0: 19.5, 2: 23.0, 3: 23.0, 4: 22.0, 5: 20.0, 6: 19.0, 7: 20.5, 9: 20.5,
    10: 18.5, 11: 24.0, 12: 28.0, 13: 22.5, 14: 21.5, 15: 21.0, 16: 21.5, 17: 20.5,
    19: 22.0, 20: 20.5, 26: 21.0, 27: 27.0, 29: 20.0, 30: 21.0, 31: 21.0, 32: 25.0,
    39: 20.5, 40: 20.5, 41: 21.0,
}
DEFAULT_PIT_LOSS_S = 21.5

# The field runs slower behind a safety car, so a stop costs a fraction of the green-flag loss
NEUTRALISED_PIT_FACTOR = {"FULL": 0.55, "VIRTUAL": 0.65}

# Rejoining closer than this to the car ahead means sitting in its dirty air
TRAFFIC_GAP_S = 1.5
# A car closer than this behind on rejoin can attack while new tyres warm up
EXPOSED_GAP_S = 1.0
# Teammates share a box: the second car waits for the first car's stop plus the release
STATIONARY_S = 2.5
BOX_RELEASE_S = 1.0


def pit_loss(track_id: int, safety_car: str = "NONE") -> float:
    loss = PIT_LOSS_S.get(track_id, DEFAULT_PIT_LOSS_S)
    return round(loss * NEUTRALISED_PIT_FACTOR.get(safety_car, 1.0), 1)


def double_stack_delay(player: dict | None, teammate: dict | None) -> float:
    """Seconds the player would queue if they boxed now behind a teammate who is in the pit lane."""
    if not player or not teammate or (teammate.get("pitStatus") or "NONE") == "NONE":
        return 0.0
    behind_by = (player.get("gapToLeader") or 0.0) - (teammate.get("gapToLeader") or 0.0)
    if behind_by < 0:
        return 0.0
    return round(max(0.0, STATIONARY_S + BOX_RELEASE_S - behind_by), 1)


def rejoin(cars: list[dict], player_idx: int, loss: float) -> dict | None:
    """Predict the player's position and neighbours if they stopped now, from gaps to the leader."""
    player = next((c for c in cars if c.get("idx") == player_idx), None)
    if player is None or not player.get("position"):
        return None
    running = [
        c for c in cars
        if c.get("idx") != player_idx and c.get("position") and c.get("resultStatus", "ACTIVE") == "ACTIVE"
    ]
    projected = (player.get("gapToLeader") or 0.0) + loss
    ahead = [c for c in running if (c.get("gapToLeader") or 0.0) <= projected]
    behind = [c for c in running if (c.get("gapToLeader") or 0.0) > projected]
    car_ahead = max(ahead, key=lambda c: c.get("gapToLeader") or 0.0, default=None)
    car_behind = min(behind, key=lambda c: c.get("gapToLeader") or 0.0, default=None)
    gap_ahead = round(projected - (car_ahead.get("gapToLeader") or 0.0), 1) if car_ahead else None
    gap_behind = round((car_behind.get("gapToLeader") or 0.0) - projected, 1) if car_behind else None
    return {
        "position": len(ahead) + 1,
        "positions_lost": len(ahead) + 1 - player["position"],
        "ahead": car_ahead.get("name") if car_ahead else None,
        "gap_ahead": gap_ahead,
        "behind": car_behind.get("name") if car_behind else None,
        "gap_behind": gap_behind,
        "traffic": gap_ahead is not None and gap_ahead < TRAFFIC_GAP_S,
        "exposed": gap_behind is not None and gap_behind < EXPOSED_GAP_S,
    }

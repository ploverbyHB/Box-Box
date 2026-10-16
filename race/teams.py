"""
Team colours (2024 F1 broadcast colours) and each team's lead race engineer, used to style captions.
Teams without a well-known engineer here use the generic label.
"""
import re

GENERIC_LABEL = "Race Engineer"

TEAM_STYLE = {
    "Mercedes": ("#27F4D2", "Peter Bonnington"),
    "Ferrari": ("#E8002D", "Bryan Bozzi"),
    "Red Bull Racing": ("#3671C6", "Gianpiero Lambiase"),
    "Williams": ("#64C4FF", "James Urwin"),
    "Aston Martin": ("#229971", ""),
    "Alpine": ("#FF87BC", ""),
    "RB": ("#6692FF", ""),
    "Haas": ("#B6BABD", ""),
    "McLaren": ("#FF8000", "Will Joseph"),
    "Sauber": ("#52E252", ""),
}


def caption_style(car: dict | None) -> tuple[str, str]:
    """Label and #RRGGBB accent for the player's team; the game's livery colour covers other teams."""
    if not car:
        return GENERIC_LABEL, ""
    # The game names season-pack teams with a year suffix: "Ferrari '26"
    team = re.sub(r"\s*'\d{2}$", "", car.get("team") or "")
    colour, engineer = TEAM_STYLE.get(team, ("", ""))
    return engineer or GENERIC_LABEL, colour or car.get("liveryColour") or ""

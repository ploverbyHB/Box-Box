"""
F1 工程模型层 — 提取自专业 f1_engineer.models，适配轻量级数据结构。

在 LLM 分析前运行，输出精确的物理数据供策略引擎使用。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum


# ============================================================================
# 枚举定义
# ============================================================================

class TireCompound(str, Enum):
    C0 = "C0"; C1 = "C1"; C2 = "C2"; C3 = "C3"; C4 = "C4"; C5 = "C5"
    SOFT = "SOFT"; MEDIUM = "MEDIUM"; HARD = "HARD"
    INTER = "INTER"; WET = "WET"
    UNKNOWN = "UNKNOWN"


class OverheatRisk(str, Enum):
    NONE = "none"; LOW = "low"; MEDIUM = "medium"; HIGH = "high"; CRITICAL = "critical"


# ============================================================================
# 轮胎模型参数 (tire/params.py)
# ============================================================================

@dataclass(frozen=True, slots=True)
class CompoundParams:
    alpha: float          # 线性老化退化 s/lap
    beta: float           # 二次老化退化 s/lap²
    kappa: float          # 完全磨损时的圈速损失 s
    a_wear: float         # 抓地力对磨损的敏感度
    t_star: float         # 最佳轮胎温度 °C
    sigma: float          # 热窗口半宽 °C
    t_hot: float          # 过热惩罚起始温度
    t_crit: float         # 临界过热温度
    t_cold: float         # 冷却/颗粒化惩罚起始温度
    expected_life_laps: float  # 预期竞技寿命 圈
    wear_cliff: float     # 磨损悬崖 (0-1)
    competitive_loss_s: float  # 失去竞争力的累计损失 s


# F1 24 轮胎系数表 (来源: formula1.wiki + simracingsetup)
_COMPOUND_DEFAULTS: dict[str, CompoundParams] = {
    "C0": CompoundParams(0.012, 0.00014, 1.40, 0.20, 100, 8.75, 110, 120, 85, 33, 0.95, 1.40),
    "C1": CompoundParams(0.015, 0.00018, 1.45, 0.22, 100, 8.75, 110, 120, 85, 30, 0.94, 1.45),
    "C2": CompoundParams(0.020, 0.00025, 1.50, 0.25,  95, 7.50, 105, 115, 85, 25, 0.93, 1.50),
    "C3": CompoundParams(0.025, 0.00035, 1.60, 0.28,  90, 7.50, 100, 110, 80, 20, 0.92, 1.60),
    "C4": CompoundParams(0.030, 0.00050, 1.75, 0.32,  90, 6.25,  97, 105, 80, 16, 0.90, 1.75),
    "C5": CompoundParams(0.035, 0.00070, 1.90, 0.36,  85, 7.50,  95, 105, 75, 12, 0.88, 1.90),
    "SOFT":  CompoundParams(0.030, 0.00050, 1.75, 0.32, 90, 6.25, 97, 105, 80, 16, 0.90, 1.75),
    "MEDIUM": CompoundParams(0.025, 0.00035, 1.60, 0.28, 90, 7.50, 100, 110, 80, 20, 0.92, 1.60),
    "HARD":  CompoundParams(0.015, 0.00018, 1.45, 0.22, 100, 8.75, 110, 120, 85, 30, 0.94, 1.45),
    "INTER": CompoundParams(0.040, 0.00060, 2.00, 0.30, 65, 5.00, 70, 75, 55, 16, 0.88, 2.00),
    "WET":   CompoundParams(0.035, 0.00045, 2.20, 0.28, 55, 5.00, 60, 65, 45, 18, 0.88, 2.20),
}

# 全局模型配置
AGE_WEAR_LAMBDA = 0.55    # 老化/磨损混合权重
PHI_MIN = 0.55            # 热形状最小值
GAMMA_HOT = 0.04          # 过热惩罚 s/°C
GAMMA_COLD = 0.03         # 冷却惩罚 s/°C
THERMAL_RATE_BOOST = 0.02 # 过热时的额外退化率


# ============================================================================
# 轮胎模型 (tire/model.py)
# ============================================================================

@dataclass
class TireResult:
    grip_factor: float
    health_score: float          # 0-100
    deg_rate_s_per_lap: float    # 下一圈的预期退化 s
    remaining_laps: float        # 剩余竞技圈数
    performance_loss_s: float    # 累计性能损失 s
    overheat_risk: OverheatRisk
    compound: str
    age_laps: int
    limiting_budget: str         # wear / age / performance / cliff


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _get_compound(compound_str: str) -> CompoundParams:
    # F1 25 C6 has no coefficients yet; C5 is the nearest compound
    key = compound_str.upper().replace("SS", "SOFT").replace("C6", "C5")
    if key in _COMPOUND_DEFAULTS:
        return _COMPOUND_DEFAULTS[key]
    return _COMPOUND_DEFAULTS["MEDIUM"]


def evaluate_tire(car: dict) -> TireResult:
    """评估单辆车的轮胎状态。"""
    compound_str = car.get("compound", "MEDIUM")
    params = _get_compound(compound_str)

    # 胎龄
    age = float(car.get("tyreAge", car.get("stintLaps", 0)) or 0)
    age = max(age, 0.0)

    # 磨损 (0-1)
    tw = car.get("tireWear", {})
    if tw:
        wears = [tw.get(k, 0) for k in ("FL", "FR", "RL", "RR")]
        wear_frac = _clamp(sum(wears) / 400.0, 0.0, 1.0)
    else:
        wear_frac = _clamp(age / max(params.expected_life_laps, 1.0), 0.0, 1.0)

    # 温度
    tt = car.get("tireTemps", {})
    temps = [tt.get(k) for k in ("FL", "FR", "RL", "RR") if tt.get(k) is not None]
    mean_temp = sum(temps) / len(temps) if temps else None
    peak_temp = max(temps) if temps else mean_temp

    # 热形状 φ(T)
    if mean_temp is not None:
        z = (mean_temp - params.t_star) / max(params.sigma, 1e-6)
        phi = _clamp(math.exp(-0.5 * z * z), PHI_MIN, 1.0)
    else:
        phi = 1.0

    # 抓地力
    grip = _clamp((1.0 - params.a_wear * wear_frac) * phi, 0.0, 1.0)

    # 性能损失 Δt
    dt_age = params.alpha * age + params.beta * (age ** 2)
    dt_wear = params.kappa * wear_frac
    dt_thermal = 0.0
    if mean_temp is not None:
        dt_thermal += GAMMA_HOT * max(0.0, mean_temp - params.t_hot)
        dt_thermal += GAMMA_COLD * max(0.0, params.t_cold - mean_temp)
    performance_loss = AGE_WEAR_LAMBDA * dt_age + (1.0 - AGE_WEAR_LAMBDA) * dt_wear + dt_thermal

    # 退化率 r
    wear_rate = wear_frac / max(age, 1.0)
    r_age = params.alpha + 2.0 * params.beta * age
    r_wear = (1.0 - AGE_WEAR_LAMBDA) * params.kappa * wear_rate
    r_therm = THERMAL_RATE_BOOST if (mean_temp is not None and mean_temp > params.t_hot) else 0.0
    deg_rate = max(0.0, r_age + r_wear + r_therm)

    # 剩余寿命
    eps = 1e-4
    if wear_frac >= params.wear_cliff or performance_loss >= params.competitive_loss_s:
        remaining = 0.0
        limiting = "cliff"
    else:
        n_wear = (params.wear_cliff - wear_frac) / max(wear_rate, eps)
        n_age = max(0.0, params.expected_life_laps - age)
        n_perf = max(0.0, (params.competitive_loss_s - performance_loss) / max(deg_rate, eps))
        remaining = min(n_wear, n_age, n_perf)
        limiting = "wear" if remaining == n_wear else ("age" if remaining == n_age else "performance")

    # 健康度
    h_grip = grip
    h_wear = 1.0 - wear_frac / max(params.wear_cliff, 1e-6)
    h_life = remaining / max(params.expected_life_laps, 1e-6)
    h_loss = 1.0 - performance_loss / max(params.competitive_loss_s, 1e-6)
    health = 100.0 * _clamp(0.35 * h_grip + 0.25 * h_wear + 0.25 * h_life + 0.15 * h_loss, 0.0, 1.0)

    # 过热风险
    if peak_temp is None:
        overheat = OverheatRisk.NONE
    elif peak_temp >= params.t_crit:
        overheat = OverheatRisk.CRITICAL
    elif peak_temp >= params.t_hot + 5.0:
        overheat = OverheatRisk.HIGH
    elif peak_temp >= params.t_hot:
        overheat = OverheatRisk.MEDIUM
    elif peak_temp >= params.t_hot - 5.0:
        overheat = OverheatRisk.LOW
    else:
        overheat = OverheatRisk.NONE

    return TireResult(
        grip_factor=round(grip, 4),
        health_score=round(health, 1),
        deg_rate_s_per_lap=round(deg_rate, 4),
        remaining_laps=round(remaining, 1),
        performance_loss_s=round(performance_loss, 3),
        overheat_risk=overheat,
        compound=compound_str,
        age_laps=int(age),
        limiting_budget=limiting,
    )


# ============================================================================
# 燃油模型参数 (fuel/params.py)
# ============================================================================

FUEL_BASE_CONSUMPTION = 1.6    # kg/lap
FUEL_S_PER_KG = 0.035          # s/kg
FUEL_REFERENCE_KG = 20.0       # 参考油量
FUEL_RESERVE_LAPS = 1.0        # 保留圈数
FUEL_RESERVE_MIN_KG = 1.5     # 最小保留油量
FUEL_SAVE_THRESHOLD = 0.5      # 必须省油阈值 圈
FUEL_PUSH_THRESHOLD = 1.5      # 可以冲刺阈值 圈


@dataclass
class FuelResult:
    consumption_kg_per_lap: float
    fuel_margin_laps: float | None    # 油量余量 (圈)
    can_push: bool | None
    must_save: bool | None
    laps_on_board: float | None       # 当前油量可跑圈数
    fuel_effect_s: float | None       # 油量对圈速的影响 s


def evaluate_fuel(car: dict, race_laps: int | None = None, current_lap: int | None = None) -> FuelResult:
    """评估燃油状态。"""
    fuel_kg = car.get("fuel")
    if fuel_kg is None:
        return FuelResult(FUEL_BASE_CONSUMPTION, None, None, None, None, None)

    # 简化消耗估算 (基于油量趋势)
    consumption = FUEL_BASE_CONSUMPTION
    laps_on_board = fuel_kg / consumption if consumption > 0 else None

    # 油量余量
    remaining_laps = None
    if race_laps is not None and current_lap is not None:
        remaining_laps = max(0, race_laps - current_lap)

    margin = None
    fuel_required = None
    can_push = None
    must_save = None

    if remaining_laps is not None and consumption > 0:
        fuel_required = remaining_laps * consumption + FUEL_RESERVE_MIN_KG
        margin = (fuel_kg - FUEL_RESERVE_MIN_KG) / consumption - float(remaining_laps)
        must_save = margin < FUEL_SAVE_THRESHOLD
        can_push = margin > FUEL_PUSH_THRESHOLD

    # 油量对圈速的影响
    fuel_effect = FUEL_S_PER_KG * (fuel_kg - FUEL_REFERENCE_KG) if fuel_kg is not None else None

    return FuelResult(
        consumption_kg_per_lap=round(consumption, 3),
        fuel_margin_laps=round(margin, 2) if margin is not None else None,
        can_push=can_push,
        must_save=must_save,
        laps_on_board=round(laps_on_board, 1) if laps_on_board is not None else None,
        fuel_effect_s=round(fuel_effect, 3) if fuel_effect is not None else None,
    )


# ============================================================================
# ERS 模式映射
# ============================================================================

ERS_MODE_DELTAS = {
    "OVERTAKE": -0.12,
    "HOTLAP": -0.06,
    "MEDIUM": 0.0,
    "NONE": 0.04,
}


# ============================================================================
# 对手威胁评估参数 (opponent/params.py)
# ============================================================================

THREAT_DRS_GAP_S = 1.0
THREAT_MEDIUM_GAP_S = 1.5
THREAT_WATCH_GAP_S = 2.5
THREAT_GAP_MAX_SCORE = 40.0
THREAT_DRS_BONUS = 12.0
THREAT_PACE_PER_S = 55.0
THREAT_PACE_CAP = 28.0
THREAT_HIGH_THRESHOLD = 55.0
THREAT_MEDIUM_THRESHOLD = 30.0
UNDERCUT_FRESH_GAIN_S = 0.85
UNDERCUT_EFFECTIVE_LAPS = 3.0


@dataclass
class OpponentThreat:
    car_idx: int
    name: str
    threat_score: float        # 0-100
    threat_level: str          # LOW / MEDIUM / HIGH
    gap_to_player: float | None
    pace_delta: float | None   # 正 = 对手更快
    laps_to_catch: float | None
    factors: list[str]


def evaluate_opponent_threat(player: dict, opponent: dict) -> OpponentThreat:
    """评估单个对手的威胁。"""
    car_idx = opponent.get("idx", -1)
    name = opponent.get("name", f"#{car_idx}")
    gap = opponent.get("gapToAhead") if opponent.get("position", 99) > player.get("position", 99) else opponent.get("gapToBehind")
    if gap is None:
        gap = opponent.get("gapToLeader", 0) - player.get("gapToLeader", 0)

    # 圈速差
    opp_lap = opponent.get("bestLapTime")
    player_lap = player.get("bestLapTime")
    pace_delta = None
    if opp_lap and player_lap and player_lap > 0:
        pace_delta = opp_lap - player_lap

    # 追上所需圈数
    laps_to_catch = None
    if gap is not None and pace_delta is not None and abs(pace_delta) > 1e-6:
        if gap > 0 and pace_delta < 0:
            laps_to_catch = gap / (-pace_delta)
        elif gap < 0 and pace_delta > 0:
            laps_to_catch = abs(gap) / pace_delta

    # 威胁评分
    score = 0.0
    factors = []
    abs_gap = abs(gap) if gap is not None else None

    if abs_gap is not None:
        fade = _clamp(1.0 - abs_gap / THREAT_WATCH_GAP_S, 0.0, 1.0)
        score += THREAT_GAP_MAX_SCORE * fade
        if abs_gap <= THREAT_DRS_GAP_S:
            score += THREAT_DRS_BONUS
            factors.append(f"gap {abs_gap:.1f}s (DRS range)")

    if gap is not None and gap > 0 and pace_delta is not None and pace_delta < 0:
        pts = min(THREAT_PACE_CAP, -pace_delta * THREAT_PACE_PER_S)
        score += pts
        if pace_delta <= -0.12:
            factors.append(f"faster by {-pace_delta:.2f}s/lap")

    if laps_to_catch is not None and laps_to_catch <= 8.0:
        frac = 1.0 - laps_to_catch / 8.0
        score += 18.0 * frac
        factors.append(f"catching in ~{laps_to_catch:.1f} laps")

    score = _clamp(score, 0.0, 100.0)
    if score >= THREAT_HIGH_THRESHOLD:
        level = "HIGH"
    elif score >= THREAT_MEDIUM_THRESHOLD:
        level = "MEDIUM"
    else:
        level = "LOW"

    return OpponentThreat(
        car_idx=car_idx,
        name=name,
        threat_score=round(score, 1),
        threat_level=level,
        gap_to_player=round(gap, 3) if gap is not None else None,
        pace_delta=round(pace_delta, 3) if pace_delta is not None else None,
        laps_to_catch=round(laps_to_catch, 1) if laps_to_catch is not None else None,
        factors=factors,
    )


# ============================================================================
# 天气/抓地力模型 (weather/params.py)
# ============================================================================

@dataclass
class WeatherResult:
    wetness: str              # DRY / DAMP / WET / FLOODED
    grip_level: float         # 0-1
    pace_loss_s: float        # 天气导致的圈速损失 s


DRY_INTENSITY_MAX = 0.08
DAMP_INTENSITY_MAX = 0.28
WET_INTENSITY_MAX = 0.65
DAMP_GRIP_LINEAR = 0.55
WET_GRIP_QUADRATIC = 0.35
STANDING_WATER_ONSET = 0.55
STANDING_WATER_CLIFF = 0.18
GRIP_FLOOR = 0.25
PACE_LOSS_AT_ZERO_GRIP = 1.5

# Telemetry frames carry weather as a label; each maps into the DAMP / WET / FLOODED bands above
WEATHER_RAIN_PERCENT = {"LIGHTRAIN": 20, "HEAVYRAIN": 50, "STORM": 80}


def evaluate_weather(track: dict) -> WeatherResult:
    """评估天气状况。"""
    weather = track.get("weather", {})
    if isinstance(weather, str):
        weather = {"rain_percent": WEATHER_RAIN_PERCENT.get(weather.upper(), 0)}
    rain_percent = weather.get("rain_percent", 0)
    intensity = _clamp(rain_percent / 100.0, 0.0, 1.0) if rain_percent else 0.0

    # 湿度分类
    if intensity < DRY_INTENSITY_MAX:
        wetness = "DRY"
    elif intensity < DAMP_INTENSITY_MAX:
        wetness = "DAMP"
    elif intensity < WET_INTENSITY_MAX:
        wetness = "WET"
    else:
        wetness = "FLOODED"

    # 抓地力
    g = 1.0 - DAMP_GRIP_LINEAR * intensity - WET_GRIP_QUADRATIC * (intensity ** 2)
    if intensity > STANDING_WATER_ONSET:
        g -= STANDING_WATER_CLIFF * ((intensity - STANDING_WATER_ONSET) / max(1.0 - STANDING_WATER_ONSET, 1e-6))
    g = _clamp(g, GRIP_FLOOR, 1.0)

    pace_loss = PACE_LOSS_AT_ZERO_GRIP * (1.0 - g)

    return WeatherResult(
        wetness=wetness,
        grip_level=round(g, 3),
        pace_loss_s=round(pace_loss, 3),
    )


# ============================================================================
# 综合分析入口
# ============================================================================

@dataclass
class TelemetryAnalysis:
    """单帧遥测的完整物理模型分析结果。"""
    player_tire: TireResult
    player_fuel: FuelResult
    player_weather: WeatherResult
    opponent_threats: list[OpponentThreat]
    race_situation: str       # 格式化的赛况摘要


def analyze_telemetry_frame(cars: list[dict], player_idx: int, lap: int, track: dict) -> TelemetryAnalysis:
    """对一帧遥测数据运行所有物理模型。"""
    player = cars[player_idx] if 0 <= player_idx < len(cars) else {}

    # 轮胎
    tire = evaluate_tire(player)

    # 燃油
    fuel = evaluate_fuel(player, race_laps=track.get("totalLaps"), current_lap=lap)

    # 天气
    weather = evaluate_weather(track)

    # 对手威胁 (取前5名最近的对手)
    threats = []
    for car in cars:
        if car.get("idx") == player_idx:
            continue
        t = evaluate_opponent_threat(player, car)
        if t.gap_to_player is not None and abs(t.gap_to_player) < 10.0:
            threats.append(t)
    threats.sort(key=lambda x: x.threat_score, reverse=True)
    threats = threats[:5]

    # 赛况摘要
    pos = player.get("position", "?")
    speed = player.get("speed", 0)
    gap_ahead = player.get("gapToAhead", 0)
    gap_behind = player.get("gapToBehind", 0)
    drs = "ON" if player.get("drs") else "OFF"
    ers = player.get("ersBattery", 0)

    situation = (
        f"P{pos} | {speed}km/h | DRS {drs} | ERS {ers:.0f}%\n"
        f"Tire: {tire.compound} L{tire.age_laps} | grip={tire.grip_factor:.2f} | "
        f"health={tire.health_score:.0f}% | loss={tire.performance_loss_s:.2f}s\n"
        f"Fuel: {player.get('fuel', 0):.1f}L | margin={fuel.fuel_margin_laps or 'n/a'} laps | "
        f"{'CAN PUSH' if fuel.can_push else 'MUST SAVE' if fuel.must_save else 'OK'}\n"
        f"Gap: ahead +{gap_ahead:.1f}s | behind -{gap_behind:.1f}s\n"
        f"Weather: {weather.wetness} | grip={weather.grip_level:.2f} | pace_loss={weather.pace_loss_s:.2f}s"
    )

    return TelemetryAnalysis(
        player_tire=tire,
        player_fuel=fuel,
        player_weather=weather,
        opponent_threats=threats,
        race_situation=situation,
    )


def format_analysis_for_llm(analysis: TelemetryAnalysis, cars: list[dict], player_idx: int, lap: int) -> str:
    """将物理模型分析格式化为 LLM 可读的结构化输入。"""
    t = analysis.player_tire
    f = analysis.player_fuel
    w = analysis.player_weather

    lines = [
        f"=== TELEMETRY ANALYSIS (Lap {lap}) ===",
        "",
        "PLAYER STATE:",
        f"  Tire: {t.compound} L{t.age_laps} | grip={t.grip_factor:.3f} | health={t.health_score:.1f}%",
        f"  Tire loss: {t.performance_loss_s:.3f}s | deg_rate: {t.deg_rate_s_per_lap:.4f}s/lap",
        f"  Remaining laps: {t.remaining_laps:.1f} | limiting: {t.limiting_budget}",
        f"  Overheat risk: {t.overheat_risk.value}",
        f"  Fuel: {f.consumption_kg_per_lap:.2f}kg/lap | margin: {f.fuel_margin_laps or 'n/a'} laps",
        f"  Fuel effect: {f.fuel_effect_s or 0:.3f}s | push={f.can_push} save={f.must_save}",
        f"  Weather grip: {w.grip_level:.3f} | pace_loss: {w.pace_loss_s:.3f}s | wetness: {w.wetness}",
        "",
        "OPPONENT THREATS:",
    ]

    for th in analysis.opponent_threats:
        lines.append(
            f"  P{th.car_idx} {th.name}: threat={th.threat_level}({th.threat_score:.0f}) "
            f"gap={th.gap_to_player or 'n/a'}s pace_delta={th.pace_delta or 'n/a'}s "
            f"catch={th.laps_to_catch or 'n/a'} laps | {', '.join(th.factors) if th.factors else 'no pressure'}"
        )

    if not analysis.opponent_threats:
        lines.append("  No immediate threats within 10s")

    lines.extend(["", "FULL STANDINGS:"])
    sorted_cars = sorted(cars, key=lambda c: c.get("position", 99))
    for c in sorted_cars:
        pos = c.get("position", "?")
        name = c.get("name", f"#{c.get('idx', '?')}")
        compound = c.get("compound", "?")
        age = c.get("tyreAge", c.get("stintLaps", 0))
        gap = c.get("gapToLeader", 0)
        lines.append(f"  P{pos} {name} | {compound} L{age} | +{gap:.1f}s")

    return "\n".join(lines)

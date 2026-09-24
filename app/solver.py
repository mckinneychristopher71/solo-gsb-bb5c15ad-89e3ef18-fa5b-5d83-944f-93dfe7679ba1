"""共窑排程核心算法。

输入 1~14 件器物（唯一编号、正整数体积、允许烧成温度闭区间）与单窑容量，
输出最少窑次的装窑方案；窑次相同时选择整体字典序最小的方案。

同窑规则：
  * 总体积不超过单窑容量；
  * 各器物温度区间存在公共交集；
  * 烧成温度取交集下界（= 各区间下界的最大值）。

算法：
  1. 枚举全部 2^n 个器物子集，递推每个子集的总体积与温度交集，判定能否成窑。
  2. 位掩码 DP 求每个掩码所需最少窑次 dp[mask]。
  3. 贪心重建字典序最小方案：每一步在剩余器物中，找到包含剩余器物
     最小编号、且取出后剩余部分仍可在剩余窑次内装完的、
     器物编号序列字典序最小的子集作为下一窑。

容量反推 solve_capacity_target：
  给定目标窑次，在 [最大单件体积, 总体积] 的整数容量区间二分查找，
  复用 solve 判定每个容量下的最少窑次，返回使窑次不超过目标的最小容量。
"""

from __future__ import annotations

from dataclasses import dataclass


class CapacityExceededError(ValueError):
    """单件器物体积超过单窑容量，无法烧成。"""


class TargetUnreachableError(ValueError):
    """即使容量放宽到总体积（温度约束成为唯一限制），仍无法达到目标窑次。"""


@dataclass(frozen=True)
class Ware:
    item_id: str
    volume: int
    temp_low: int
    temp_high: int


@dataclass(frozen=True)
class Kiln:
    """一窑：装入的器物编号（升序）、总体积、烧成温度。"""

    item_ids: tuple[str, ...]
    total_volume: int
    firing_temp: int


@dataclass(frozen=True)
class CapacityTarget:
    """容量反推结果：满足目标窑次的最小容量、实际窑次与装窑明细。"""

    capacity: int
    kiln_count: int
    kilns: tuple[Kiln, ...]


def solve(items: list[Ware], capacity: int) -> list[Kiln]:
    """求最少窑次、字典序最小的装窑方案。

    器物编号按标准字符串字典序排列（如 "1" < "10" < "2"），
    窑内编号升序、窑次间的整体字典序比较均以此为准。

    :raises CapacityExceededError: 存在单件体积超过容量的器物。
    :raises ValueError: 器物列表为空或超过 14 件。
    """

    n = len(items)
    if n == 0:
        raise ValueError("至少需要一件器物")
    if n > 14:
        raise ValueError("器物数量不得超过 14 件")

    # 器物按编号字典序升序排列；位 i 对应排序后的第 i 件。
    ordered = sorted(items, key=lambda it: it.item_id)
    ids = [it.item_id for it in ordered]
    volumes = [it.volume for it in ordered]
    low = [it.temp_low for it in ordered]
    high = [it.temp_high for it in ordered]

    oversized = [it.item_id for it in ordered if it.volume > capacity]
    if oversized:
        raise CapacityExceededError(
            f"以下器物单件体积超过单窑容量 {capacity}，无法烧成: "
            + ", ".join(oversized)
        )

    size = 1 << n

    # ---- 1. 每个子集的总体积、温度交集与可行性（低位加一件递推） ----
    valid = bytearray(size)
    mask_volume = [0] * size
    mask_temp_low = [0] * size   # 交集下界（烧成温度）
    mask_temp_high = [0] * size  # 交集上界
    for mask in range(1, size):
        bit = mask & -mask  # 最低位
        i = bit.bit_length() - 1
        prev = mask ^ bit
        total = mask_volume[prev] + volumes[i]
        if prev == 0:
            inter_low, inter_high = low[i], high[i]
            feasible = volumes[i] <= capacity
        else:
            inter_low = max(mask_temp_low[prev], low[i])
            inter_high = min(mask_temp_high[prev], high[i])
            feasible = (
                total <= capacity
                and inter_low <= inter_high
                and bool(valid[prev])
            )
        mask_volume[mask] = total
        mask_temp_low[mask] = inter_low
        mask_temp_high[mask] = inter_high
        if feasible:
            valid[mask] = 1

    # ---- 2. 位掩码 DP：最少窑次 ----
    # dp[0] = 0；dp[mask] = 1 + min(dp[mask ^ g])，g 为 mask 的非空可行子集
    dp = [n + 1] * size
    dp[0] = 0
    for mask in range(1, size):
        best = dp[mask]
        sub = mask
        while sub:
            if valid[sub]:
                candidate = dp[mask ^ sub] + 1
                if candidate < best:
                    best = candidate
            sub = (sub - 1) & mask
        dp[mask] = best

    # ---- 3. 贪心重建字典序最小方案 ----
    full = size - 1
    allowed_kilns = dp[full]
    left = full
    kilns: list[Kiln] = []

    while left:
        first = (left & -left).bit_length() - 1  # 剩余器物中编号最小者
        rest_bits = left ^ (1 << first)

        # 枚举包含 first 的所有子集，在“取出后剩余器物能在
        # allowed_kilns-1 窑内装完”的可行组中，取器物编号序列字典序最小者。
        best_group = 0
        best_tuple: tuple[str, ...] | None = None
        sub_rest = rest_bits
        while True:
            group = sub_rest | (1 << first)
            if valid[group] and dp[left ^ group] <= allowed_kilns - 1:
                members = tuple(
                    ids[i] for i in range(n) if (group >> i) & 1
                )
                if best_tuple is None or members < best_tuple:
                    best_tuple = members
                    best_group = group
            if sub_rest == 0:
                break
            sub_rest = (sub_rest - 1) & rest_bits

        kilns.append(
            Kiln(
                item_ids=best_tuple,
                total_volume=mask_volume[best_group],
                firing_temp=mask_temp_low[best_group],
            )
        )
        left ^= best_group
        allowed_kilns -= 1

    return kilns


def solve_capacity_target(items: list[Ware], target_kiln_count: int) -> CapacityTarget:
    """反推使最少窑次不超过目标的最小单窑容量。

    搜索范围为 [最大单件体积, 全部器物总体积] 的整数容量：容量低于最大单件
    体积时必无法烧成；容量等于总体积时容量约束完全松弛，窑次仅由温度区间
    交集决定。容量越大可同窑的器物越多，最少窑次关于容量单调不增，故使用
    二分查找。每个候选容量均调用 solve 判定，沿用同一套同窑温区规则与
    同窑次方案排序（字典序）规则。

    :raises TargetUnreachableError: 总体积容量下最少窑次仍超过目标。
    :raises ValueError: 器物列表为空 / 超过 14 件，或目标窑次不在
        1～器物数之间。
    """

    n = len(items)
    if n == 0:
        raise ValueError("至少需要一件器物")
    if n > 14:
        raise ValueError("器物数量不得超过 14 件")
    if not 1 <= target_kiln_count <= n:
        raise ValueError(f"目标窑次必须在 1～{n} 之间")

    max_volume = max(it.volume for it in items)
    total_volume = sum(it.volume for it in items)

    cache: dict[int, list[Kiln]] = {}

    def run(capacity: int) -> list[Kiln]:
        if capacity not in cache:
            cache[capacity] = solve(items, capacity)
        return cache[capacity]

    # 总体积容量下仅受温度约束；仍达不到目标则任何容量都不可行。
    at_total = run(total_volume)
    if len(at_total) > target_kiln_count:
        raise TargetUnreachableError(
            f"目标窑次 {target_kiln_count} 不可达：即使单窑容量放宽到总体积 "
            f"{total_volume}（温度区间约束成为唯一限制），最少仍需 "
            f"{len(at_total)} 窑"
        )

    lo, hi = max_volume, total_volume
    while lo < hi:
        mid = (lo + hi) // 2
        if len(run(mid)) <= target_kiln_count:
            hi = mid
        else:
            lo = mid + 1

    kilns = run(lo)
    return CapacityTarget(capacity=lo, kiln_count=len(kilns), kilns=tuple(kilns))

"""
URLs mirrored from NyxBot's ApiUrl.java for compatibility.
"""

WARFRAME_DATA_SOURCE_ALIAS = [
    "https://testingcf.jsdelivr.net/gh/KingPrimes/DataSource/warframe/alias.json",
    "https://jsd.onmicrosoft.cn/gh/KingPrimes/DataSource/warframe/alias.json",
    "https://cdn.jsdelivr.net/gh/KingPrimes/DataSource/warframe/alias.json",
    "https://kingprimes.top/warframe/alias.json",
]

WARFRAME_DATA_SOURCE_MARKET_RIVEN_TION = [
    "https://testingcf.jsdelivr.net/gh/KingPrimes/DataSource/warframe/market_riven_tion.json",
    "https://jsd.onmicrosoft.cn/gh/KingPrimes/DataSource/warframe/market_riven_tion.json",
    "https://cdn.jsdelivr.net/gh/KingPrimes/DataSource/warframe/market_riven_tion.json",
    "https://kingprimes.top/warframe/market_riven_tion.json",
]

WARFRAME_DATA_SOURCE_MARKET_RIVEN_TION_ALIAS = [
    "https://testingcf.jsdelivr.net/gh/KingPrimes/DataSource/warframe/market_riven_tion_alias.json",
    "https://jsd.onmicrosoft.cn/gh/KingPrimes/DataSource/warframe/market_riven_tion_alias.json",
    "https://cdn.jsdelivr.net/gh/KingPrimes/DataSource/warframe/market_riven_tion_alias.json",
    "https://kingprimes.top/warframe/market_riven_tion_alias.json",
]

WARFRAME_DATA_SOURCE_NODES = [
    "https://testingcf.jsdelivr.net/gh/KingPrimes/DataSource/warframe/nodes.json",
    "https://jsd.onmicrosoft.cn/gh/KingPrimes/DataSource/warframe/nodes.json",
    "https://cdn.jsdelivr.net/gh/KingPrimes/DataSource/warframe/nodes.json",
    "https://kingprimes.top/warframe/nodes.json",
]

WARFRAME_DATA_SOURCE_REWARD_POOL = [
    "https://testingcf.jsdelivr.net/gh/KingPrimes/DataSource/warframe/reward_pool.json",
    "https://jsd.onmicrosoft.cn/gh/KingPrimes/DataSource/warframe/reward_pool.json",
    "https://cdn.jsdelivr.net/gh/KingPrimes/DataSource/warframe/reward_pool.json",
    "https://kingprimes.top/warframe/reward_pool.json",
]

WARFRAME_DATA_SOURCE_RIVEN_ANALYSE_TREND = [
    "https://testingcf.jsdelivr.net/gh/KingPrimes/DataSource/warframe/riven_analyse_trend.json",
    "https://jsd.onmicrosoft.cn/gh/KingPrimes/DataSource/warframe/riven_analyse_trend.json",
    "https://cdn.jsdelivr.net/gh/KingPrimes/DataSource/warframe/riven_analyse_trend.json",
    "https://kingprimes.top/warframe/riven_analyse_trend.json",
]

WARFRAME_DATA_SOURCE_STATE_TRANSLATION = [
    "https://testingcf.jsdelivr.net/gh/KingPrimes/DataSource/warframe/state_translation.json",
    "https://jsd.onmicrosoft.cn/gh/KingPrimes/DataSource/warframe/state_translation.json",
    "https://cdn.jsdelivr.net/gh/KingPrimes/DataSource/warframe/state_translation.json",
    "https://kingprimes.top/warframe/state_translation.json",
]

# SolNode name mapping (WarframeStat)
WARFRAME_DATA_SOURCE_SOLNODES = [
    "https://api.warframestat.us/solNodes?language=zh",
    # Fallback proxy (returns a text header + JSON; handled by HttpResponse.json fallback parser)
    "https://r.jina.ai/http://api.warframestat.us/solNodes?language=zh",
    "https://r.jina.ai/https://api.warframestat.us/solNodes?language=zh",
]

# Cycles (WarframeStat, PC)
WARFRAME_DATA_SOURCE_EARTH_CYCLE = [
    "https://api.warframestat.us/pc/earthCycle?language=zh",
    "https://api.warframestat.us/pc/earthCycle",
    "https://api.warframestat.us/pc/earthCycle?language=en",
    "http://api.warframestat.us/pc/earthCycle?language=zh",
    "http://api.warframestat.us/pc/earthCycle",
    "http://api.warframestat.us/pc/earthCycle?language=en",
    "https://r.jina.ai/http://api.warframestat.us/pc/earthCycle?language=zh",
    "https://r.jina.ai/http://api.warframestat.us/pc/earthCycle",
    "https://r.jina.ai/http://api.warframestat.us/pc/earthCycle?language=en",
    "https://r.jina.ai/https://api.warframestat.us/pc/earthCycle?language=zh",
    "https://r.jina.ai/https://api.warframestat.us/pc/earthCycle",
    "https://r.jina.ai/https://api.warframestat.us/pc/earthCycle?language=en",
]
WARFRAME_DATA_SOURCE_CETUS_CYCLE = [
    "https://api.warframestat.us/pc/cetusCycle?language=zh",
    "https://api.warframestat.us/pc/cetusCycle",
    "https://api.warframestat.us/pc/cetusCycle?language=en",
    "http://api.warframestat.us/pc/cetusCycle?language=zh",
    "http://api.warframestat.us/pc/cetusCycle",
    "http://api.warframestat.us/pc/cetusCycle?language=en",
    "https://r.jina.ai/http://api.warframestat.us/pc/cetusCycle?language=zh",
    "https://r.jina.ai/http://api.warframestat.us/pc/cetusCycle",
    "https://r.jina.ai/http://api.warframestat.us/pc/cetusCycle?language=en",
    "https://r.jina.ai/https://api.warframestat.us/pc/cetusCycle?language=zh",
    "https://r.jina.ai/https://api.warframestat.us/pc/cetusCycle",
    "https://r.jina.ai/https://api.warframestat.us/pc/cetusCycle?language=en",
]
WARFRAME_DATA_SOURCE_VALLIS_CYCLE = [
    "https://api.warframestat.us/pc/vallisCycle?language=zh",
    "https://api.warframestat.us/pc/vallisCycle",
    "https://api.warframestat.us/pc/vallisCycle?language=en",
    "http://api.warframestat.us/pc/vallisCycle?language=zh",
    "http://api.warframestat.us/pc/vallisCycle",
    "http://api.warframestat.us/pc/vallisCycle?language=en",
    "https://r.jina.ai/http://api.warframestat.us/pc/vallisCycle?language=zh",
    "https://r.jina.ai/http://api.warframestat.us/pc/vallisCycle",
    "https://r.jina.ai/http://api.warframestat.us/pc/vallisCycle?language=en",
    "https://r.jina.ai/https://api.warframestat.us/pc/vallisCycle?language=zh",
    "https://r.jina.ai/https://api.warframestat.us/pc/vallisCycle",
    "https://r.jina.ai/https://api.warframestat.us/pc/vallisCycle?language=en",
]
WARFRAME_DATA_SOURCE_CAMBION_CYCLE = [
    "https://api.warframestat.us/pc/cambionCycle?language=zh",
    "https://api.warframestat.us/pc/cambionCycle",
    "https://api.warframestat.us/pc/cambionCycle?language=en",
    "http://api.warframestat.us/pc/cambionCycle?language=zh",
    "http://api.warframestat.us/pc/cambionCycle",
    "http://api.warframestat.us/pc/cambionCycle?language=en",
    "https://r.jina.ai/http://api.warframestat.us/pc/cambionCycle?language=zh",
    "https://r.jina.ai/http://api.warframestat.us/pc/cambionCycle",
    "https://r.jina.ai/http://api.warframestat.us/pc/cambionCycle?language=en",
    "https://r.jina.ai/https://api.warframestat.us/pc/cambionCycle?language=zh",
    "https://r.jina.ai/https://api.warframestat.us/pc/cambionCycle",
    "https://r.jina.ai/https://api.warframestat.us/pc/cambionCycle?language=en",
]
WARFRAME_DATA_SOURCE_ZARIMAN_CYCLE = [
    "https://api.warframestat.us/pc/zarimanCycle?language=zh",
    "https://api.warframestat.us/pc/zarimanCycle",
    "https://api.warframestat.us/pc/zarimanCycle?language=en",
    "http://api.warframestat.us/pc/zarimanCycle?language=zh",
    "http://api.warframestat.us/pc/zarimanCycle",
    "http://api.warframestat.us/pc/zarimanCycle?language=en",
    "https://r.jina.ai/http://api.warframestat.us/pc/zarimanCycle?language=zh",
    "https://r.jina.ai/http://api.warframestat.us/pc/zarimanCycle",
    "https://r.jina.ai/http://api.warframestat.us/pc/zarimanCycle?language=en",
    "https://r.jina.ai/https://api.warframestat.us/pc/zarimanCycle?language=zh",
    "https://r.jina.ai/https://api.warframestat.us/pc/zarimanCycle",
    "https://r.jina.ai/https://api.warframestat.us/pc/zarimanCycle?language=en",
]
WARFRAME_DATA_SOURCE_DUVIRI_CYCLE = [
    "https://api.warframestat.us/pc/duviriCycle?language=zh",
    "https://api.warframestat.us/pc/duviriCycle",
    "https://api.warframestat.us/pc/duviriCycle?language=en",
    "http://api.warframestat.us/pc/duviriCycle?language=zh",
    "http://api.warframestat.us/pc/duviriCycle",
    "http://api.warframestat.us/pc/duviriCycle?language=en",
    "https://r.jina.ai/http://api.warframestat.us/pc/duviriCycle?language=zh",
    "https://r.jina.ai/http://api.warframestat.us/pc/duviriCycle",
    "https://r.jina.ai/http://api.warframestat.us/pc/duviriCycle?language=en",
    "https://r.jina.ai/https://api.warframestat.us/pc/duviriCycle?language=zh",
    "https://r.jina.ai/https://api.warframestat.us/pc/duviriCycle",
    "https://r.jina.ai/https://api.warframestat.us/pc/duviriCycle?language=en",
]


"""配置持久化：把乐器、编排参数、成本模型、调度参数存成一个 JSON。

设计目标是「一处修改，处处生效」——想定向优化时只改这个文件即可，
不必碰代码。
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, fields
from typing import Any, Dict, Optional, Tuple

from .arrange import Options
from .fingering import CostModel
from .instrument import Instrument
from .player import SchedulerConfig

DEFAULT_FILENAME = 'gtiharmonica.json'


@dataclass
class Config:
    """全部可调项。"""

    instrument: Dict[str, Any] = field(
        default_factory=lambda: Instrument().export_config())
    options: Dict[str, Any] = field(default_factory=lambda: _options_dict(Options()))
    cost: Dict[str, Any] = field(default_factory=lambda: asdict(CostModel()))
    scheduler: Dict[str, Any] = field(
        default_factory=lambda: asdict(SchedulerConfig()))

    # -- 构建运行时对象 --

    def build(self) -> Tuple[Instrument, Options, CostModel, SchedulerConfig]:
        instrument = Instrument.from_config(self.instrument)

        valid = {f.name for f in fields(Options)} - {'cost'}
        opts_kwargs = {k: v for k, v in self.options.items() if k in valid}
        options = Options(**opts_kwargs)
        options.cost = self.build_cost()

        cost = self.build_cost()
        valid_sched = {f.name for f in fields(SchedulerConfig)}
        scheduler = SchedulerConfig(**{k: v for k, v in self.scheduler.items()
                                       if k in valid_sched})
        return instrument, options, cost, scheduler

    def build_cost(self) -> CostModel:
        valid = {f.name for f in fields(CostModel)}
        return CostModel(**{k: v for k, v in self.cost.items() if k in valid})

    # -- 读写 --

    def save(self, path: str) -> None:
        with open(path, 'w', encoding='utf8') as fh:
            json.dump(asdict(self), fh, ensure_ascii=False, indent=1)

    @classmethod
    def load(cls, path: str) -> 'Config':
        with open(path, encoding='utf8') as fh:
            data = json.load(fh)
        cfg = cls()
        for key in ('instrument', 'options', 'cost', 'scheduler'):
            if isinstance(data.get(key), dict):
                getattr(cfg, key).update(data[key])
        return cfg

    @classmethod
    def load_or_default(cls, path: Optional[str]) -> 'Config':
        if path and os.path.exists(path):
            return cls.load(path)
        # 顺手兼容原程序的 instrument.json
        if path is None and os.path.exists('instrument.json'):
            try:
                with open('instrument.json', encoding='utf8') as fh:
                    cfg = cls()
                    cfg.instrument.update(json.load(fh))
                    return cfg
            except Exception:
                pass
        return cls()


def _options_dict(opts: Options) -> Dict[str, Any]:
    data = asdict(opts)
    data.pop('cost', None)          # cost 单独存
    return data


def find_config(explicit: Optional[str] = None) -> Config:
    """按 explicit → 当前目录 → 用户目录 的顺序找配置。"""
    if explicit:
        return Config.load(explicit)
    if os.path.exists(DEFAULT_FILENAME):
        return Config.load(DEFAULT_FILENAME)
    home = os.path.join(os.path.expanduser('~'), '.' + DEFAULT_FILENAME)
    if os.path.exists(home):
        return Config.load(home)
    return Config()

"""Optional AetherCore training/research observer.

Production inference modules do not import this package.  Integrators opt in by
constructing a :class:`ResearchObserver` around a research-only sink.
"""

from aethersparse.observer.analysis import analyze_records, counterfactual_analysis
from aethersparse.observer.capture import summarize_hidden_state
from aethersparse.observer.counterfactual import CounterfactualRunner
from aethersparse.observer.registry import load_registry, seal_registry, write_registry
from aethersparse.observer.sampling import DeterministicSampler, SamplingPolicy
from aethersparse.observer.store import JsonlObserverSink, NullObserverSink, ResearchObserver

__all__ = [
    "CounterfactualRunner",
    "DeterministicSampler",
    "JsonlObserverSink",
    "NullObserverSink",
    "ResearchObserver",
    "SamplingPolicy",
    "analyze_records",
    "counterfactual_analysis",
    "load_registry",
    "seal_registry",
    "summarize_hidden_state",
    "write_registry",
]

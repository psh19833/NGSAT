"""NGSAT common exceptions."""

from __future__ import annotations


class NGSATError(Exception):
    """Base exception for all NGSAT errors."""
    pass


class ConfigError(NGSATError):
    """Configuration related error."""
    pass


class DataError(NGSATError):
    """Data collection or storage error."""
    pass


class BrokerError(NGSATError):
    """Securities broker API error."""
    pass


class StrategyError(NGSATError):
    """Strategy evaluation error."""
    pass


class MLModelError(NGSATError):
    """Machine learning model error."""
    pass


class OrderError(NGSATError):
    """Order execution error."""
    pass


class RiskLimitHit(NGSATError):
    """Risk limit has been reached — trading must stop."""
    pass


class DecisionError(NGSATError):
    """Missing or invalid trading decision reason."""
    pass

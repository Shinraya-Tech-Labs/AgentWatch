"""AgentWatch v3 sensors.

Sensors capture raw observations (payload + identifiers declared by the source) and
hand them to a sink. They never interpret behaviour, never block and never raise into
the observed application.
"""

from panoptis.sensors.base import (
    FileSink,
    HttpSink,
    ListSink,
    ObservationSink,
    Sensor,
    SensorContext,
)

# LLM provider sensors (lazy imports to avoid hard dependencies)
try:
    from panoptis.sensors.groq_sensor import GroqSensor, instrument_groq
except Exception:  # noqa: BLE001
    GroqSensor = None  # type: ignore
    instrument_groq = None  # type: ignore

try:
    from panoptis.sensors.openrouter_sensor import OpenRouterSensor, instrument_openrouter
except Exception:  # noqa: BLE001
    OpenRouterSensor = None  # type: ignore
    instrument_openrouter = None  # type: ignore

try:
    from panoptis.sensors.llm_clients import (
        AnthropicClientSensor,
        OpenAIClientSensor,
        instrument_anthropic,
        instrument_openai,
    )
except Exception:  # noqa: BLE001
    AnthropicClientSensor = None  # type: ignore
    OpenAIClientSensor = None  # type: ignore
    instrument_anthropic = None  # type: ignore
    instrument_openai = None  # type: ignore

__all__ = [
    "FileSink",
    "HttpSink",
    "ListSink",
    "ObservationSink",
    "Sensor",
    "SensorContext",
    "GroqSensor",
    "instrument_groq",
    "OpenRouterSensor",
    "instrument_openrouter",
    "AnthropicClientSensor",
    "OpenAIClientSensor",
    "instrument_anthropic",
    "instrument_openai",
]

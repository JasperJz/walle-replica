"""
Recognition pipelines that orchestrate the full workflow.

Pipelines combine cameras, recognizers, and debouncers to provide
end-to-end gesture recognition.
"""

from .gesture_pipeline import GesturePipeline

__all__ = ["GesturePipeline"]

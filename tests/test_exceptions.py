"""All analysis failures must remain catchable by the shared handler."""

import pytest
from app.exceptions import (
    AnalysisError,
    ColorAnalysisError,
    GeminiAnalysisError,
    InvalidImageError,
    MissingApiKeyError,
    NoCatDetectedError,
    RejectedImageError,
    SegmentationError,
)


@pytest.mark.parametrize(
    "error",
    [
        ColorAnalysisError("color"),
        GeminiAnalysisError("gemini"),
        InvalidImageError("image"),
        MissingApiKeyError("key"),
        NoCatDetectedError("cat"),
        RejectedImageError("quality", "rejected"),
        SegmentationError("segmentation"),
    ],
)
def test_analysis_errors_share_one_base_class(error):
    with pytest.raises(AnalysisError):
        raise error

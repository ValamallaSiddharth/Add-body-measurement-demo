from datetime import datetime
from pydantic import BaseModel, Field

class MeasurementOut(BaseModel):
    id: str
    person_id: str = Field(validation_alias="external_id")
    height_cm: float
    shoulder_cm: float
    waist_cm: float
    front_waist_width_cm: float
    side_waist_depth_cm: float
    confidence: float
    status: str
    notes: str | None
    created_at: datetime
    front_image_filename: str | None = Field(default=None, validation_alias="front_image_path")
    side_image_filename: str | None = Field(default=None, validation_alias="side_image_path")

    model_config = {"from_attributes": True}

"""主数据 schema：客户 / 承运商 / 车辆 / 司机（基线文档 §7.4 T04–T07、§10.3【主数据】）。

出参统一：手机号与联系邮箱脱敏（§8.7），时间带 Z。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.masking import mask_email, mask_phone
from app.models.enums import CarrierStatus, CustomerLevel, DriverStatus, VehicleStatus
from app.schemas.common import OptUtcDateTime, UtcDateTime


# --- 客户 --------------------------------------------------------------------
class CustomerCreate(BaseModel):
    code: str = Field(min_length=1, max_length=32)
    name: str = Field(min_length=1, max_length=128)
    level: CustomerLevel = CustomerLevel.NORMAL
    contact_name: str | None = Field(default=None, max_length=64)
    contact_phone: str | None = Field(default=None, max_length=32)
    contact_email: str | None = Field(default=None, max_length=128)
    notify_pref: str = Field(default="MANUAL_COPY", max_length=32)
    remark: str | None = Field(default=None, max_length=255)
    status: str = Field(default="ACTIVE", max_length=16)


class CustomerUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    level: CustomerLevel | None = None
    contact_name: str | None = Field(default=None, max_length=64)
    contact_phone: str | None = Field(default=None, max_length=32)
    contact_email: str | None = Field(default=None, max_length=128)
    notify_pref: str | None = Field(default=None, max_length=32)
    remark: str | None = Field(default=None, max_length=255)
    status: str | None = Field(default=None, max_length=16)
    expected_version: int | None = None


class CustomerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    workspace_id: int
    code: str
    name: str
    level: str
    contact_name: str | None = None
    contact_phone: str | None = None
    contact_email: str | None = None
    notify_pref: str
    remark: str | None = None
    status: str
    version: int
    created_at: UtcDateTime
    updated_at: OptUtcDateTime = None

    @model_validator(mode="after")
    def _mask_contacts(self) -> CustomerOut:
        """出参一律脱敏（§8.7），无论从 ORM 还是从 from_model 进来。"""
        self.contact_phone = mask_phone(self.contact_phone)
        self.contact_email = mask_email(self.contact_email)
        return self

    @classmethod
    def from_model(cls, customer: object) -> CustomerOut:
        return cls(
            id=customer.id,  # type: ignore[attr-defined]
            workspace_id=customer.workspace_id,  # type: ignore[attr-defined]
            code=customer.code,  # type: ignore[attr-defined]
            name=customer.name,  # type: ignore[attr-defined]
            level=str(customer.level),  # type: ignore[attr-defined]
            contact_name=getattr(customer, "contact_name", None),
            contact_phone=mask_phone(getattr(customer, "contact_phone", None)),
            contact_email=mask_email(getattr(customer, "contact_email", None)),
            notify_pref=getattr(customer, "notify_pref", "MANUAL_COPY"),
            remark=getattr(customer, "remark", None),
            status=str(getattr(customer, "status", "ACTIVE")),
            version=int(getattr(customer, "version", 1)),
            created_at=customer.created_at,
            updated_at=getattr(customer, "updated_at", None),
        )


# --- 承运商 ------------------------------------------------------------------
class CarrierCreate(BaseModel):
    code: str = Field(min_length=1, max_length=32)
    name: str = Field(min_length=1, max_length=128)
    contact_name: str | None = Field(default=None, max_length=64)
    contact_phone: str | None = Field(default=None, max_length=32)
    service_level: str = Field(default="NORMAL", max_length=16)
    status: CarrierStatus = CarrierStatus.ACTIVE
    remark: str | None = Field(default=None, max_length=255)


class CarrierUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    contact_name: str | None = Field(default=None, max_length=64)
    contact_phone: str | None = Field(default=None, max_length=32)
    service_level: str | None = Field(default=None, max_length=16)
    status: CarrierStatus | None = None
    remark: str | None = Field(default=None, max_length=255)
    expected_version: int | None = None


class CarrierOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    workspace_id: int
    code: str
    name: str
    contact_name: str | None = None
    contact_phone: str | None = None
    service_level: str
    status: str
    remark: str | None = None
    version: int
    created_at: UtcDateTime
    updated_at: OptUtcDateTime = None

    @model_validator(mode="after")
    def _mask_contact(self) -> CarrierOut:
        self.contact_phone = mask_phone(self.contact_phone)
        return self

    @classmethod
    def from_model(cls, carrier: object) -> CarrierOut:
        return cls(
            id=carrier.id,  # type: ignore[attr-defined]
            workspace_id=carrier.workspace_id,  # type: ignore[attr-defined]
            code=carrier.code,  # type: ignore[attr-defined]
            name=carrier.name,  # type: ignore[attr-defined]
            contact_name=getattr(carrier, "contact_name", None),
            contact_phone=mask_phone(getattr(carrier, "contact_phone", None)),
            service_level=getattr(carrier, "service_level", "NORMAL"),
            status=str(getattr(carrier, "status", "ACTIVE")),
            remark=getattr(carrier, "remark", None),
            version=int(getattr(carrier, "version", 1)),
            created_at=carrier.created_at,
            updated_at=getattr(carrier, "updated_at", None),
        )


# --- 车辆 --------------------------------------------------------------------
class VehicleCreate(BaseModel):
    plate_no: str = Field(min_length=1, max_length=16)
    vehicle_type: str | None = Field(default=None, max_length=32)
    capacity_ton: float | None = Field(default=None, ge=0, le=9999)
    carrier_id: int | None = None
    status: VehicleStatus = VehicleStatus.IDLE
    current_driver_id: int | None = None
    current_driver_name: str | None = Field(
        default=None,
        max_length=64,
        description="主驾司机姓名（手输，按承运商匹配；一名司机只能绑定一台车）",
    )
    current_city: str | None = Field(default=None, max_length=64)
    remark: str | None = Field(default=None, max_length=255)


class VehicleUpdate(BaseModel):
    plate_no: str | None = Field(default=None, min_length=1, max_length=16)
    vehicle_type: str | None = Field(default=None, max_length=32)
    capacity_ton: float | None = Field(default=None, ge=0, le=9999)
    carrier_id: int | None = None
    status: VehicleStatus | None = None
    current_driver_id: int | None = None
    current_driver_name: str | None = Field(
        default=None, max_length=64, description="主驾司机姓名（手输；传 null/空串表示解除绑定）"
    )
    current_city: str | None = Field(default=None, max_length=64)
    remark: str | None = Field(default=None, max_length=255)
    expected_version: int | None = None


class VehicleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    workspace_id: int
    plate_no: str
    vehicle_type: str | None = None
    capacity_ton: float | None = None
    carrier_id: int | None = None
    status: str
    current_driver_id: int | None = None
    current_city: str | None = None
    remark: str | None = None
    version: int
    created_at: UtcDateTime
    updated_at: OptUtcDateTime = None

    @classmethod
    def from_model(cls, vehicle: object) -> VehicleOut:
        capacity = getattr(vehicle, "capacity_ton", None)
        return cls(
            id=vehicle.id,  # type: ignore[attr-defined]
            workspace_id=vehicle.workspace_id,  # type: ignore[attr-defined]
            plate_no=vehicle.plate_no,  # type: ignore[attr-defined]
            vehicle_type=getattr(vehicle, "vehicle_type", None),
            capacity_ton=float(capacity) if capacity is not None else None,
            carrier_id=getattr(vehicle, "carrier_id", None),
            status=str(getattr(vehicle, "status", "IDLE")),
            current_driver_id=getattr(vehicle, "current_driver_id", None),
            current_city=getattr(vehicle, "current_city", None),
            remark=getattr(vehicle, "remark", None),
            version=int(getattr(vehicle, "version", 1)),
            created_at=vehicle.created_at,
            updated_at=getattr(vehicle, "updated_at", None),
        )


# --- 司机 --------------------------------------------------------------------
class DriverCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    phone: str | None = Field(default=None, max_length=32)
    carrier_id: int | None = None
    license_no: str | None = Field(default=None, max_length=32)
    status: DriverStatus = DriverStatus.AVAILABLE


class DriverUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    phone: str | None = Field(default=None, max_length=32)
    carrier_id: int | None = None
    license_no: str | None = Field(default=None, max_length=32)
    status: DriverStatus | None = None
    expected_version: int | None = None


class DriverOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    workspace_id: int
    name: str
    phone: str | None = None
    carrier_id: int | None = None
    license_no: str | None = None
    status: str
    version: int
    created_at: UtcDateTime
    updated_at: OptUtcDateTime = None

    @model_validator(mode="after")
    def _mask_phone(self) -> DriverOut:
        self.phone = mask_phone(self.phone)
        return self

    @classmethod
    def from_model(cls, driver: object) -> DriverOut:
        return cls(
            id=driver.id,  # type: ignore[attr-defined]
            workspace_id=driver.workspace_id,  # type: ignore[attr-defined]
            name=driver.name,  # type: ignore[attr-defined]
            phone=mask_phone(getattr(driver, "phone", None)),
            carrier_id=getattr(driver, "carrier_id", None),
            license_no=getattr(driver, "license_no", None),
            status=str(getattr(driver, "status", "AVAILABLE")),
            version=int(getattr(driver, "version", 1)),
            created_at=driver.created_at,
            updated_at=getattr(driver, "updated_at", None),
        )


__all__ = [
    "CarrierCreate",
    "CarrierOut",
    "CarrierUpdate",
    "CustomerCreate",
    "CustomerOut",
    "CustomerUpdate",
    "DriverCreate",
    "DriverOut",
    "DriverUpdate",
    "VehicleCreate",
    "VehicleOut",
    "VehicleUpdate",
]

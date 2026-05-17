"""Diagnose endpoint — POST /api/v1/diagnose."""
import uuid
from datetime import datetime

from fastapi import APIRouter

from app.api.deps import DB
from app.models.diagnostic import DiagnosticReport
from app.schemas.diagnostic import CompatibilityIssue, DiagnoseResponse, DiagnosticReportSchema
from app.compatibility.errors import (
    IncompatibilityError,
    UnknownVersionError,
    UnsupportedOSError,
)
from app.compatibility.models import PackageConstraint
from app.compatibility.resolver import CompatibilityResolver
from app.schemas.profile import ProfileFilters
from app.services.profile_service import list_profiles

router = APIRouter()


@router.post("/diagnose", response_model=DiagnoseResponse, status_code=201)
async def diagnose(
    report: DiagnosticReportSchema,
    db: DB,
) -> DiagnoseResponse:
    """
    Accept a DiagnosticReport from the CLI agent and return
    a compatibility analysis: which profiles are compatible,
    and what issues were found.
    """
    # Persist the raw report
    db_report = DiagnosticReport(
        id=uuid.uuid4(),
        report_data=report.model_dump(),
        os_type=report.os.name.split()[0].upper()[:5] if report.os else None,
        gpu_name=report.gpus[0].name if report.gpus else None,
        cuda_version=report.cuda.version if report.cuda else None,
        rocm_version=report.rocm.version if report.rocm else None,
        python_version=report.active_python.version[:4] if report.active_python else None,
        driver_version=report.gpus[0].driver_version if report.gpus else None,
        created_at=datetime.utcnow(),
    )
    db.add(db_report)
    await db.flush()

    issues: list[CompatibilityIssue] = []
    compatible_profiles: list[str] = []
    recommendations: list[str] = []

    profiles, _ = await list_profiles(db, ProfileFilters())
    resolver = CompatibilityResolver()

    for profile in profiles:
        packages = [
            PackageConstraint(
                name=package.package_name,
                version_spec=package.version_spec,
                cuda_variant=package.cuda_variant,
                is_optional=package.is_optional,
                install_order=package.install_order,
            )
            for package in sorted(profile.packages, key=lambda item: item.install_order)
        ]

        try:
            resolved = resolver.resolve(
                packages=packages,
                python_version=report.active_python.version if report.active_python else None,
                cuda_version=report.cuda.version if report.cuda else None,
                rocm_version=report.rocm.version if report.rocm else None,
                target_os=report.os.name.split()[0].upper()[:5] if report.os else None,
                profile_slug=profile.slug,
                os_support=profile.os_support,
                cuda_required=profile.cuda_required,
                rocm_required=profile.rocm_required,
            )

            compatible_profiles.append(profile.slug)

            if resolved.warnings:
                recommendations.extend(resolved.warnings)

        except IncompatibilityError as exc:
            issues.append(
                CompatibilityIssue(
                    severity="ERROR",
                    component=exc.component,
                    message=str(exc),
                    suggested_fix=exc.suggestion,
                    docs_url=exc.docs_url,
                )
            )

        except (UnknownVersionError, UnsupportedOSError) as exc:
            issues.append(
                CompatibilityIssue(
                    severity="ERROR",
                    component="compatibility",
                    message=str(exc),
                    suggested_fix=None,
                    docs_url=None,
                )
            )

        return DiagnoseResponse(
            report_id=str(db_report.id),
            compatible_profiles=compatible_profiles,
            issues=issues,
            recommendations=recommendations,
        )
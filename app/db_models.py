"""Import every model module so SQLAlchemy mappers and Alembic autogenerate see all tables.

Add new model modules here.
"""

from app.core import counters
from app.core.base import Base
from app.modules.audit import models as audit_models
from app.modules.auth import models as auth_models
from app.modules.coaches import models as coach_models
from app.modules.fees import models as fee_models
from app.modules.files import models as file_models
from app.modules.notifications import models as notification_models
from app.modules.payment_submissions import models as submission_models
from app.modules.performance import models as performance_models
from app.modules.reference import models as reference_models
from app.modules.sessions import models as session_models
from app.modules.students import models as student_models
from app.modules.users import models as user_models
from app.modules.website import models as website_models

__all__ = [
    "Base",
    "audit_models",
    "auth_models",
    "coach_models",
    "counters",
    "fee_models",
    "file_models",
    "notification_models",
    "performance_models",
    "reference_models",
    "session_models",
    "student_models",
    "submission_models",
    "user_models",
    "website_models",
]

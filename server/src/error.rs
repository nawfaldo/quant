use actix_web::{HttpResponse, ResponseError, http::StatusCode};
use serde_json::json;

#[derive(Debug, thiserror::Error)]
pub enum ApiError {
    #[error("{0}")]
    BadRequest(String),
    #[error("{0}")]
    NotFound(String),
    #[error("{0}")]
    Conflict(String),
    #[error("{0}")]
    MethodNotAllowed(String),
    #[error("market data is unavailable: {0}")]
    QuestDb(String),
    #[error("ORM operation failed: {0}")]
    SeaOrm(#[from] sea_orm::DbErr),
    #[error("internal error: {0}")]
    Internal(String),
}

impl ApiError {
    pub fn internal(error: impl std::fmt::Display) -> Self {
        Self::Internal(error.to_string())
    }
}

impl ResponseError for ApiError {
    fn status_code(&self) -> StatusCode {
        match self {
            Self::BadRequest(_) => StatusCode::BAD_REQUEST,
            Self::NotFound(_) => StatusCode::NOT_FOUND,
            Self::Conflict(_) => StatusCode::CONFLICT,
            Self::MethodNotAllowed(_) => StatusCode::METHOD_NOT_ALLOWED,
            Self::QuestDb(_) => StatusCode::SERVICE_UNAVAILABLE,
            Self::SeaOrm(_) | Self::Internal(_) => StatusCode::INTERNAL_SERVER_ERROR,
        }
    }

    fn error_response(&self) -> HttpResponse {
        if self.status_code().is_server_error() {
            tracing::error!(error = %self, "request failed");
        }
        HttpResponse::build(self.status_code()).json(json!({ "error": self.to_string() }))
    }
}

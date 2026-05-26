// Package models contains Go structs that mirror the reportes_db tables
// created by manejador_reportes Django migrations.
// Table names and column names match exactly so pgx raw SQL works without ORM.
package models

import (
	"time"

	"github.com/google/uuid"
)

// ── eventos_entrantes ─────────────────────────────────────────────────────────

// EventoEntrante mirrors the eventos_entrantes table used for idempotency.
type EventoEntrante struct {
	ID          uuid.UUID  `db:"id"`
	EventoID    string     `db:"evento_id"` // unique idempotency key (AMQP message_id)
	TipoEvento  string     `db:"tipo_evento"`
	Payload     []byte     `db:"payload"` // JSONB stored as raw bytes
	Procesado   bool       `db:"procesado"`
	RecibidoEn  time.Time  `db:"recibido_en"`
	ProcesadoEn *time.Time `db:"procesado_en"` // nullable
}

// ── analisis ─────────────────────────────────────────────────────────────────

// Analisis mirrors the analisis table.
type Analisis struct {
	ID            uuid.UUID `db:"id"`
	Nombre        string    `db:"nombre"`
	ProyectoID    uuid.UUID `db:"proyecto_id"`
	EmpresaID     uuid.UUID `db:"empresa_id"`
	Tipo          string    `db:"tipo"`   // COSTO | CAPACIDAD | OPTIMIZACION | DESPERDICIO
	Estado        string    `db:"estado"` // PENDIENTE | EN_PROCESO | COMPLETADO | FALLIDO
	CreadoEn      time.Time `db:"creado_en"`
	ActualizadoEn time.Time `db:"actualizado_en"`
}

// ── ejecuciones_analisis ──────────────────────────────────────────────────────

// EjecucionAnalisis mirrors the ejecuciones_analisis table.
type EjecucionAnalisis struct {
	ID           uuid.UUID  `db:"id"`
	AnalisisID   uuid.UUID  `db:"analisis_id"`
	Estado       string     `db:"estado"` // EN_PROCESO | COMPLETADO | FALLIDO
	IniciadoEn   time.Time  `db:"iniciado_en"`
	CompletadoEn *time.Time `db:"completado_en"` // nullable
	DuracionMs   *int64     `db:"duracion_ms"`   // nullable
	Resultado    []byte     `db:"resultado"`     // JSONB
}

// ── reportes ─────────────────────────────────────────────────────────────────

// Reporte mirrors the reportes table.
type Reporte struct {
	ID            uuid.UUID `db:"id"`
	Nombre        string    `db:"nombre"`
	Tipo          string    `db:"tipo"` // MENSUAL | ANUAL | PROYECTO | AREA
	ProyectoID    uuid.UUID `db:"proyecto_id"`
	EmpresaID     uuid.UUID `db:"empresa_id"`
	PeriodoInicio time.Time `db:"periodo_inicio"`
	PeriodoFin    time.Time `db:"periodo_fin"`
	Datos         []byte    `db:"datos"` // JSONB
	GeneradoEn    time.Time `db:"generado_en"`
}

// ── alertas ──────────────────────────────────────────────────────────────────

// Alerta mirrors the alertas table.
type Alerta struct {
	ID         uuid.UUID `db:"id"`
	AnalisisID uuid.UUID `db:"analisis_id"`
	ReporteID  uuid.UUID `db:"reporte_id"`
	Tipo       string    `db:"tipo"` // PRESUPUESTO | ANOMALIA | RECURSO_INFRAUTILIZADO | PICO_CONSUMO
	Mensaje    string    `db:"mensaje"`
	Severidad  string    `db:"severidad"` // BAJA | MEDIA | ALTA | CRITICA
	Resuelta   bool      `db:"resuelta"`
	CreadaEn   time.Time `db:"creada_en"`
}

// ── notificaciones ────────────────────────────────────────────────────────────

// Notificacion mirrors the notificaciones table.
type Notificacion struct {
	ID                  uuid.UUID  `db:"id"`
	EjecucionAnalisisID *uuid.UUID `db:"ejecucion_analisis_id"` // nullable FK
	UsuarioID           uuid.UUID  `db:"usuario_id"`
	EmailDestino        string     `db:"email_destino"`
	Tipo                string     `db:"tipo"` // EMAIL | PUSH | WEBHOOK
	Asunto              string     `db:"asunto"`
	Cuerpo              string     `db:"cuerpo"`
	Enviada             bool       `db:"enviada"`
	EnviadaEn           *time.Time `db:"enviada_en"` // nullable
	CreadaEn            time.Time  `db:"creada_en"`
}

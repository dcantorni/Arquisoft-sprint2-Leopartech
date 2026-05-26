// Package db provides a pgx/v5 connection pool and helper insert functions
// for each reportes_db table used by the Golang worker.
package db

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"strconv"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"

	"worker_golang/models"
)

// Pool is the shared pgx connection pool.
var Pool *pgxpool.Pool

// Connect initialises the connection pool from environment variables.
// Call once during startup; the returned pool is stored in Pool.
func Connect(ctx context.Context) (*pgxpool.Pool, error) {
	host := getenv("DATABASE_HOST", "localhost")
	port := getenv("DATABASE_PORT", "5432")
	name := getenv("DATABASE_NAME", "reportes_db")
	user := getenv("DATABASE_USER", "admin")
	pass := getenv("DATABASE_PASSWORD", "admin123")

	maxConns, _ := strconv.Atoi(getenv("DATABASE_MAX_CONNS", "20"))

	dsn := fmt.Sprintf(
		"postgres://%s:%s@%s:%s/%s?sslmode=disable&pool_max_conns=%d",
		user, pass, host, port, name, maxConns,
	)

	cfg, err := pgxpool.ParseConfig(dsn)
	if err != nil {
		return nil, fmt.Errorf("pgxpool.ParseConfig: %w", err)
	}
	cfg.MaxConns = int32(maxConns)
	cfg.MinConns = 2
	cfg.MaxConnLifetime = 30 * time.Minute
	cfg.MaxConnIdleTime = 5 * time.Minute
	cfg.HealthCheckPeriod = 60 * time.Second

	pool, err := pgxpool.NewWithConfig(ctx, cfg)
	if err != nil {
		return nil, fmt.Errorf("pgxpool.NewWithConfig: %w", err)
	}
	if err = pool.Ping(ctx); err != nil {
		return nil, fmt.Errorf("pool.Ping: %w", err)
	}

	Pool = pool
	return pool, nil
}

// ── EventoEntrante ───────────────────────────────────────────────────────────

// IsAlreadyProcessed returns true if the evento_id already exists as procesado.
func IsAlreadyProcessed(ctx context.Context, tx pgx.Tx, eventoID string) (bool, error) {
	var count int
	err := tx.QueryRow(ctx,
		`SELECT COUNT(*) FROM eventos_entrantes WHERE evento_id = $1 AND estado = 'procesado'`,
		eventoID,
	).Scan(&count)
	return count > 0, err
}

// InsertEventoEntrante inserts a new incoming event record (estado=recibido).
func InsertEventoEntrante(ctx context.Context, tx pgx.Tx, eventoID, tipoEvento string, payload []byte) (uuid.UUID, error) {
	id := uuid.New()
	_, err := tx.Exec(ctx,
		`INSERT INTO eventos_entrantes (id, evento_id, tipo_evento, payload, estado, recibido_en)
		 VALUES ($1, $2, $3, $4, 'recibido', NOW())
		 ON CONFLICT (evento_id) DO NOTHING`,
		id, eventoID, tipoEvento, payload,
	)
	return id, err
}

// MarkEventoProcessed marks an evento_id as procesado.
func MarkEventoProcessed(ctx context.Context, tx pgx.Tx, eventoID string) error {
	_, err := tx.Exec(ctx,
		`UPDATE eventos_entrantes SET estado = 'procesado', procesado_en = NOW()
		 WHERE evento_id = $1`,
		eventoID,
	)
	return err
}

// ── Analisis ─────────────────────────────────────────────────────────────────

// InsertAnalisis creates a new Analisis row and returns its UUID.
func InsertAnalisis(ctx context.Context, tx pgx.Tx, proyectoID, empresaID uuid.UUID, nombre, tipo string) (uuid.UUID, error) {
	id := uuid.New()
	_, err := tx.Exec(ctx,
		`INSERT INTO analisis (id, nombre, proyecto_id, empresa_id, tipo, estado, creado_en, actualizado_en)
		 VALUES ($1, $2, $3, $4, $5, 'EN_PROCESO', NOW(), NOW())`,
		id, nombre, proyectoID, empresaID, tipo,
	)
	return id, err
}

// CompleteAnalisis transitions an Analisis to COMPLETADO using the pool (post-commit).
func CompleteAnalisis(ctx context.Context, pool *pgxpool.Pool, id uuid.UUID) error {
	_, err := pool.Exec(ctx,
		`UPDATE analisis SET estado = 'COMPLETADO', actualizado_en = NOW() WHERE id = $1`,
		id,
	)
	return err
}

// ── EjecucionAnalisis ────────────────────────────────────────────────────────

// InsertEjecucion creates an EjecucionAnalisis row with estado=EN_PROCESO.
func InsertEjecucion(ctx context.Context, tx pgx.Tx, analisisID uuid.UUID) (uuid.UUID, error) {
	id := uuid.New()
	_, err := tx.Exec(ctx,
		`INSERT INTO ejecuciones_analisis (id, analisis_id, estado, iniciado_en)
		 VALUES ($1, $2, 'EN_PROCESO', NOW())`,
		id, analisisID,
	)
	return id, err
}

// CompleteEjecucion transitions to COMPLETADO and sets duration + result.
func CompleteEjecucion(ctx context.Context, pool *pgxpool.Pool, id uuid.UUID, duracionMs int64, resultado interface{}) error {
	res, err := json.Marshal(resultado)
	if err != nil {
		return err
	}
	_, err = pool.Exec(ctx,
		`UPDATE ejecuciones_analisis
		 SET estado = 'COMPLETADO', completado_en = NOW(), duracion_ms = $2, resultado = $3
		 WHERE id = $1`,
		id, duracionMs, res,
	)
	return err
}

// ── Reporte ──────────────────────────────────────────────────────────────────

// InsertReporte creates a Reporte row and returns its UUID.
func InsertReporte(ctx context.Context, tx pgx.Tx, proyectoID, empresaID uuid.UUID, periodoInicio, periodoFin time.Time, datos interface{}) (uuid.UUID, error) {
	id := uuid.New()
	datosJSON, err := json.Marshal(datos)
	if err != nil {
		return uuid.Nil, err
	}
	_, err = tx.Exec(ctx,
		`INSERT INTO reportes (id, proyecto_id, empresa_id, periodo_inicio, periodo_fin, datos_reporte, generado_en)
		 VALUES ($1, $2, $3, $4, $5, $6, NOW())`,
		id, proyectoID, empresaID, periodoInicio, periodoFin, datosJSON,
	)
	return id, err
}

// ── Alerta ───────────────────────────────────────────────────────────────────

// InsertAlerta creates an Alerta row linked to analisis + reporte.
func InsertAlerta(ctx context.Context, tx pgx.Tx, analisisID, reporteID uuid.UUID, tipo, mensaje, severidad string) (uuid.UUID, error) {
	id := uuid.New()
	_, err := tx.Exec(ctx,
		`INSERT INTO alertas (id, analisis_id, reporte_id, tipo, mensaje, severidad, creada_en)
		 VALUES ($1, $2, $3, $4, $5, $6, NOW())`,
		id, analisisID, reporteID, tipo, mensaje, severidad,
	)
	return id, err
}

// ── Notificacion ─────────────────────────────────────────────────────────────

// InsertNotificacion creates a pending Notificacion row.
func InsertNotificacion(ctx context.Context, pool *pgxpool.Pool, n models.Notificacion) (uuid.UUID, error) {
	id := uuid.New()
	_, err := pool.Exec(ctx,
		`INSERT INTO notificaciones
		 (id, ejecucion_analisis_id, usuario_id, email_destino, tipo, asunto, cuerpo, enviada, creada_en)
		 VALUES ($1, $2, $3, $4, $5, $6, $7, false, NOW())`,
		id,
		n.EjecucionAnalisisID,
		n.UsuarioID,
		n.EmailDestino,
		n.Tipo,
		n.Asunto,
		n.Cuerpo,
	)
	return id, err
}

// ── helpers ──────────────────────────────────────────────────────────────────

func getenv(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}

# ── Stage 1: build ────────────────────────────────────────────────────────────
FROM golang:1.21-alpine AS builder

WORKDIR /app

# Download dependencies (go.sum is generated at build time via GOFLAGS)
ENV GONOSUMDB=*
ENV GOFLAGS=-mod=mod

COPY go.mod ./
RUN go mod download

COPY . .
RUN CGO_ENABLED=0 GOOS=linux go build -trimpath -ldflags="-s -w" -o worker_golang .

# ── Stage 2: runtime ──────────────────────────────────────────────────────────
FROM alpine:3.19

RUN apk --no-cache add ca-certificates tzdata

WORKDIR /app

COPY --from=builder /app/worker_golang .

# Health endpoint
EXPOSE 8006

CMD ["./worker_golang"]

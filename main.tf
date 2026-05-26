# =============================================================================
# BITE.co Cloud Cost Management Platform
# Terraform deployment for AWS Academy
#
# Architecture reference: architecture.md §3 Deployment Architecture
# Experiments:
#   - ASR16 (Latencia)    → API Gateway → ALB usuarios → manejador_usuarios → POST /projects
#   - ASR17 (Escalabilidad) → API Gateway → ALB reportes → manejador_reportes → POST /events/batch
#                              → RabbitMQ → Worker Pool (Celery)
#   - ASR2  (Integridad)  → API Gateway → manejador_seguridad → /security/*
#   - ASR3  (Seguridad)   → API Gateway → manejador_autenticacion → /auth/*
#
# Instance sizing: t3.micro / t3.small (cheapest viable for AWS Academy)
#
# CHANGE LOG (refactor):
#   CHANGE 1 — Replaced single ALB with API Gateway + 2 internal ALBs
#   CHANGE 2 — manejador_usuarios and manejador_reportes now run in ASGs
#   CHANGE 3 — Added RDS read replica for cloud_db (CQRS read path)
#   CHANGE 4 — Redis restricted to manejador_autenticacion + manejador_cloud only
#   CHANGE 5 — Added Lambda cloud_collector + EventBridge schedule
# =============================================================================

# -----------------------------------------------------------------------------
# VARIABLES - fill before applying
# -----------------------------------------------------------------------------

variable "region" {
  description = "AWS region for deployment"
  type        = string
  default     = "us-east-1"
}

variable "project_prefix" {
  description = "Prefix used for naming all AWS resources"
  type        = string
  default     = "bite2"
}


variable "allowed_ssh_cidr" {
  description = "CIDR allowed for SSH access. Restrict to your IP in production."
  type        = string
  default     = "0.0.0.0/0"
}

variable "repository" {
  description = "Git repository URL (HTTPS) containing the Django microservices"
  type        = string
  default     = "https://github.com/dcantorni/Arquisoft-sprint2-Leopartech"
}

variable "branch" {
  description = "Git branch to deploy"
  type        = string
  default     = "main"
}

variable "celery_worker_concurrency" {
  description = "Concurrent message handlers for worker_golang (AMQP prefetch / goroutine pool)"
  type        = number
  default     = 10
}

variable "simulate_slow_processing" {
  description = "When true, worker_golang sleeps 3s per event to demo ASR15 slow-path notifications"
  type        = string
  default     = "false"
}

# Instance types - kept at the smallest viable size for AWS Academy budget
variable "instance_type_app" {
  description = "EC2 type for Django app servers (manejador_usuarios, manejador_cloud, manejador_reportes)"
  type        = string
  default     = "t3.small"
}

variable "instance_type_support" {
  description = "EC2 type for shared infrastructure: Redis and RabbitMQ"
  type        = string
  default     = "t3.micro"
}

variable "instance_type_worker" {
  description = "EC2 type for Celery worker pool instances (ASR17 scalability)"
  type        = string
  default     = "t3.small"
}

# -----------------------------------------------------------------------------
# PROVIDER & DATA SOURCES
# -----------------------------------------------------------------------------

provider "aws" {
  region = var.region
}

# CHANGE 5 – archive provider needed for Lambda zip packaging
provider "archive" {}


data "aws_vpc" "default" {
  default = true
}

data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
  filter {
    name   = "availability-zone"
    values = ["us-east-1a", "us-east-1b"]
  }
}

# Ubuntu 22.04 LTS - matches architecture.md deployment spec
data "aws_ami" "ubuntu" {
  most_recent = true
  owners      = ["099720109477"] # Canonical

  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd/ubuntu-jammy-22.04-amd64-server-*"]
  }

  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}

# -----------------------------------------------------------------------------
# LOCALS
# -----------------------------------------------------------------------------

locals {
  project_name = "${var.project_prefix}-cloud-cost-platform"
  repo_dir     = "/opt/biteco"

  common_tags = {
    Project   = local.project_name
    ManagedBy = "Terraform"
  }

  # Django ALLOWED_HOSTS: '*' is ignored when DEBUG=False; include ALB DNS + internal names.
  # Auth uses the same list — private IP not included here to avoid Terraform cycle
  # (auth instance user_data ↔ auth private_ip). Reportes middleware falls back to local JWT
  # when auth validate returns non-200 (e.g. Host header = private IP).
  django_allowed_hosts = "localhost,127.0.0.1,${aws_lb.main.dns_name},.elb.amazonaws.com,.amazonaws.com"

  # Build and run worker_golang (ASR15 async consumer — replaces Celery)
  worker_golang_bootstrap = <<-SCRIPT
    GO_VERSION=1.21.13
    curl -fsSL https://go.dev/dl/go$${GO_VERSION}.linux-amd64.tar.gz -o /tmp/go.tar.gz
    sudo rm -rf /usr/local/go
    sudo tar -C /usr/local -xzf /tmp/go.tar.gz
    export PATH=$PATH:/usr/local/go/bin
    cd ${local.repo_dir}/worker_golang
    /usr/local/go/bin/go mod download
    CGO_ENABLED=0 /usr/local/go/bin/go build -trimpath -ldflags="-s -w" -o worker_golang .
  SCRIPT

  # Shared startup script: clones the repo and waits for dependencies
  # Usage: interpolate after setting env vars in each user_data block
  git_bootstrap = <<-SCRIPT
    sudo apt-get update -y
    sudo apt-get install -y python3-pip git build-essential libpq-dev python3-dev postgresql-client netcat-openbsd
    if [ ! -d "${local.repo_dir}/.git" ]; then
      sudo git clone ${var.repository} ${local.repo_dir}
    fi
    cd ${local.repo_dir}
    git fetch origin ${var.branch} || true
    git checkout ${var.branch} || true
    git pull origin ${var.branch} || true
    sudo python3 -m pip install --upgrade pip
  SCRIPT
}

# -----------------------------------------------------------------------------
# SECURITY GROUPS
# architecture.md §3 - each tier has its own SG with minimal ingress rules
# -----------------------------------------------------------------------------

resource "aws_security_group" "ssh" {
  name        = "${var.project_prefix}-ssh"
  description = "SSH access for all instances"
  vpc_id      = data.aws_vpc.default.id

  ingress {
    description = "SSH"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = [var.allowed_ssh_cidr]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(local.common_tags, { Name = "${var.project_prefix}-ssh" })
}

# CHANGE 6 – Single public-facing ALB replaces API Gateway + internal ALBs.
# This SG now serves the single internet-facing ALB. HTTP from 0.0.0.0/0 so
# the S3-hosted frontend and external clients can reach it.
# The existing aws_security_group.app ingress rules still reference this SG id.
resource "aws_security_group" "alb" {
  name        = "${var.project_prefix}-alb"
  description = "Public ALB - HTTP ingress from internet"
  vpc_id      = data.aws_vpc.default.id

  ingress {
    description = "HTTP from internet (public ALB)"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(local.common_tags, { Name = "${var.project_prefix}-alb" })
}

# App servers - only accept traffic from the ALB SG and SSH
resource "aws_security_group" "app" {
  name        = "${var.project_prefix}-app"
  description = "Django app servers - accepts from ALB only"
  vpc_id      = data.aws_vpc.default.id

  ingress {
    description     = "manejador_usuarios from ALB"
    from_port       = 8001
    to_port         = 8001
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
  }

  ingress {
    description     = "manejador_cloud from ALB (CHANGE 6)"
    from_port       = 8002
    to_port         = 8002
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
  }

  ingress {
    description = "manejador_cloud from VPC (inter-service calls)"
    from_port   = 8002
    to_port     = 8002
    protocol    = "tcp"
    cidr_blocks = [data.aws_vpc.default.cidr_block]
  }

  ingress {
    description     = "manejador_reportes from ALB"
    from_port       = 8003
    to_port         = 8003
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(local.common_tags, { Name = "${var.project_prefix}-app" })
}

# Databases - only reachable from within the VPC
resource "aws_security_group" "db" {
  name        = "${var.project_prefix}-db"
  description = "PostgreSQL - VPC-internal only"
  vpc_id      = data.aws_vpc.default.id

  ingress {
    description = "PostgreSQL from VPC"
    from_port   = 5432
    to_port     = 5432
    protocol    = "tcp"
    cidr_blocks = [data.aws_vpc.default.cidr_block]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(local.common_tags, { Name = "${var.project_prefix}-db" })
}

# Redis - VPC-internal only
resource "aws_security_group" "cache" {
  name        = "${var.project_prefix}-cache"
  description = "Redis - VPC-internal only"
  vpc_id      = data.aws_vpc.default.id

  ingress {
    description = "Redis from VPC"
    from_port   = 6379
    to_port     = 6379
    protocol    = "tcp"
    cidr_blocks = [data.aws_vpc.default.cidr_block]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(local.common_tags, { Name = "${var.project_prefix}-cache" })
}

# RabbitMQ - VPC-internal AMQP + management UI
resource "aws_security_group" "broker" {
  name        = "${var.project_prefix}-broker"
  description = "RabbitMQ - VPC-internal AMQP and management UI"
  vpc_id      = data.aws_vpc.default.id

  ingress {
    description = "AMQP from VPC"
    from_port   = 5672
    to_port     = 5672
    protocol    = "tcp"
    cidr_blocks = [data.aws_vpc.default.cidr_block]
  }

  ingress {
    description = "RabbitMQ Management UI from VPC"
    from_port   = 15672
    to_port     = 15672
    protocol    = "tcp"
    cidr_blocks = [data.aws_vpc.default.cidr_block]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(local.common_tags, { Name = "${var.project_prefix}-broker" })
}

# Worker pool - no inbound HTTP needed, only SSH and VPC egress
resource "aws_security_group" "worker" {
  name        = "${var.project_prefix}-worker"
  description = "Celery worker pool - SSH only, full VPC egress"
  vpc_id      = data.aws_vpc.default.id

  ingress {
    description = "SSH"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = [var.allowed_ssh_cidr]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(local.common_tags, { Name = "${var.project_prefix}-worker" })
}

# Auth services SG - ports 8004 (autenticacion) and 8005 (seguridad)
resource "aws_security_group" "auth" {
  name        = "${var.project_prefix}-auth"
  description = "Auth services (manejador_autenticacion + manejador_seguridad)"
  vpc_id      = data.aws_vpc.default.id

  ingress {
    description     = "manejador_autenticacion from ALB"
    from_port       = 8004
    to_port         = 8004
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
  }

  ingress {
    description = "manejador_autenticacion from VPC (inter-service)"
    from_port   = 8004
    to_port     = 8004
    protocol    = "tcp"
    cidr_blocks = [data.aws_vpc.default.cidr_block]
  }

  ingress {
    description     = "manejador_seguridad from ALB (CHANGE 6)"
    from_port       = 8005
    to_port         = 8005
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
  }

  ingress {
    description = "manejador_seguridad from VPC (inter-service)"
    from_port   = 8005
    to_port     = 8005
    protocol    = "tcp"
    cidr_blocks = [data.aws_vpc.default.cidr_block]
  }

  ingress {
    description = "SSH"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = [var.allowed_ssh_cidr]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(local.common_tags, { Name = "${var.project_prefix}-auth" })
}

# CHANGE 5 – Lambda security group: egress-only (Lambda needs to reach RDS and Redis)
resource "aws_security_group" "lambda" {
  name        = "${var.project_prefix}-lambda"
  description = "Lambda functions - egress only, reaches RDS and Redis inside VPC"
  vpc_id      = data.aws_vpc.default.id

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(local.common_tags, { Name = "${var.project_prefix}-lambda" })
}

# -----------------------------------------------------------------------------
# SHARED INFRASTRUCTURE
# architecture.md §3.5 - Redis (Elasticache) + RabbitMQ (AMQP)
# Using EC2 for AWS Academy compatibility (Elasticache requires VPC config)
# CHANGE 4: Redis is now used ONLY by manejador_autenticacion (db 0) and
#           manejador_cloud (db 1). All other services no longer reference it.
# -----------------------------------------------------------------------------

resource "aws_instance" "redis" {
  ami                         = data.aws_ami.ubuntu.id
  instance_type               = var.instance_type_support
  subnet_id                   = element(tolist(data.aws_subnets.default.ids), 0)
  associate_public_ip_address = true
  vpc_security_group_ids      = [aws_security_group.cache.id, aws_security_group.ssh.id]


  root_block_device {
    volume_size = 10
    volume_type = "gp3"
  }

  user_data = <<-EOT
    #!/bin/bash
    set -euxo pipefail
    export DEBIAN_FRONTEND=noninteractive
    sudo apt-get update -y
    sudo apt-get install -y redis-server
    # Allow connections from entire VPC
    sudo sed -i 's/^bind 127.0.0.1/bind 0.0.0.0/' /etc/redis/redis.conf
    sudo sed -i 's/^protected-mode yes/protected-mode no/' /etc/redis/redis.conf
    # LRU eviction policy - matches docker-compose config
    echo "maxmemory 256mb" | sudo tee -a /etc/redis/redis.conf
    echo "maxmemory-policy allkeys-lru" | sudo tee -a /etc/redis/redis.conf
    sudo systemctl enable redis-server
    sudo systemctl restart redis-server
  EOT

  tags = merge(local.common_tags, {
    Name    = "${var.project_prefix}-redis"
    Role    = "cache"
    Service = "redis"
  })
}

resource "aws_instance" "rabbitmq" {
  ami                         = data.aws_ami.ubuntu.id
  instance_type               = var.instance_type_support
  subnet_id                   = element(tolist(data.aws_subnets.default.ids), 0)
  associate_public_ip_address = true
  vpc_security_group_ids      = [aws_security_group.broker.id, aws_security_group.ssh.id]


  root_block_device {
    volume_size = 10
    volume_type = "gp3"
  }

  user_data = <<-EOT
    #!/bin/bash
    set -euxo pipefail
    export DEBIAN_FRONTEND=noninteractive
    sudo apt-get update -y
    sudo apt-get install -y rabbitmq-server
    sudo systemctl enable rabbitmq-server
    sudo systemctl start rabbitmq-server
    # Enable management UI
    sudo rabbitmq-plugins enable rabbitmq_management
    # Create vhost and user matching docker-compose credentials
    sudo rabbitmqctl add_vhost bite_vhost || true
    sudo rabbitmqctl add_user bite bite_pass || true
    sudo rabbitmqctl set_user_tags bite administrator || true
    sudo rabbitmqctl set_permissions -p bite_vhost bite ".*" ".*" ".*" || true
    sudo systemctl restart rabbitmq-server
  EOT

  tags = merge(local.common_tags, {
    Name    = "${var.project_prefix}-rabbitmq"
    Role    = "broker"
    Service = "rabbitmq"
  })
}

# -----------------------------------------------------------------------------
# DATABASES - Single RDS PostgreSQL instance shared across all microservices
# Each service gets its own database and dedicated user for isolation.
# RDS does not count against EC2 instance quota.
# -----------------------------------------------------------------------------

resource "aws_db_subnet_group" "main" {
  name       = "${var.project_prefix}-rds-subnets"
  subnet_ids = tolist(data.aws_subnets.default.ids)
  tags       = merge(local.common_tags, { Name = "${var.project_prefix}-rds-subnets" })
}

resource "aws_db_instance" "main" {
  identifier             = "${var.project_prefix}-postgres"
  engine                 = "postgres"
  engine_version         = "15"
  instance_class         = "db.t3.micro"
  allocated_storage      = 20
  storage_type           = "gp2"
  db_name                = "bite_master"
  username               = "bite_master"
  password               = "Bite_Master_2024!"
  db_subnet_group_name   = aws_db_subnet_group.main.name
  vpc_security_group_ids = [aws_security_group.db.id]
  skip_final_snapshot    = true
  publicly_accessible    = false
  deletion_protection    = false
  backup_retention_period = 1  # required for read replica (aws_db_instance.cloud_read_replica)

  tags = merge(local.common_tags, {
    Name = "${var.project_prefix}-postgres"
    Role = "database"
  })
}

# CHANGE 3 — CQRS read replica for cloud_db reads.
# manejador_cloud reads from DATABASE_READ_HOST (this replica).
# Lambda cloud_collector writes to DATABASE_HOST (primary).
#
# NOTE: RDS read replicas require backup_retention_period >= 1 on the source.
# If terraform apply fails with "must have automated backups enabled", run:
#   aws rds modify-db-instance --db-instance-identifier <primary-id> \
#     --backup-retention-period 1 --apply-immediately
resource "aws_db_instance" "cloud_read_replica" {
  identifier             = "${var.project_prefix}-cloud-read-replica"
  replicate_source_db    = aws_db_instance.main.identifier
  instance_class         = "db.t3.micro"
  publicly_accessible    = false
  skip_final_snapshot    = true
  vpc_security_group_ids = [aws_security_group.db.id]

  tags = merge(local.common_tags, {
    Name = "${var.project_prefix}-cloud-read-replica"
    Role = "read-replica"
  })
}

# -----------------------------------------------------------------------------
# APPLICATION SERVERS (fixed EC2 instances — no ASG)
# manejador_usuarios and manejador_reportes moved to ASGs below (CHANGE 2).
# manejador_cloud, manejador_autenticacion, manejador_seguridad stay as EC2.
# -----------------------------------------------------------------------------

# CHANGE 3 + CHANGE 4:
#   - Added DATABASE_READ_HOST pointing to cloud read replica (CQRS read path)
#   - Kept REDIS_URL=redis://...:/1 (manejador_cloud is one of the two allowed Redis users)
resource "aws_instance" "manejador_cloud" {
  ami                         = data.aws_ami.ubuntu.id
  instance_type               = var.instance_type_app
  subnet_id                   = element(tolist(data.aws_subnets.default.ids), 0)
  associate_public_ip_address = true
  vpc_security_group_ids      = [aws_security_group.app.id, aws_security_group.ssh.id]


  root_block_device {
    volume_size = 20
    volume_type = "gp3"
  }

  depends_on = [
    aws_db_instance.main,
    aws_instance.redis,
  ]

  user_data = <<-EOT
    #!/bin/bash
    set -euxo pipefail
    export DEBIAN_FRONTEND=noninteractive

    sudo tee /etc/environment <<ENV
    DATABASE_HOST=${aws_db_instance.main.address}
    DATABASE_READ_HOST=${aws_db_instance.cloud_read_replica.address}
    DATABASE_PORT=5432
    DATABASE_NAME=cloud_db
    DATABASE_USER=cloud_user
    DATABASE_PASSWORD=Cloud_2024!
    REDIS_URL=redis://${aws_instance.redis.private_ip}:6379/1
    ALLOWED_HOSTS=*
    DEBUG=True
    SECRET_KEY=bite-terraform-secret-key
    ENV

    export DATABASE_HOST=${aws_db_instance.main.address}
    export DATABASE_READ_HOST=${aws_db_instance.cloud_read_replica.address}
    export DATABASE_PORT=5432
    export DATABASE_NAME=cloud_db
    export DATABASE_USER=cloud_user
    export DATABASE_PASSWORD='Cloud_2024!'
    export REDIS_URL=redis://${aws_instance.redis.private_ip}:6379/1
    export ALLOWED_HOSTS=*
    export DEBUG=True
    export SECRET_KEY=bite-terraform-secret-key

    ${local.git_bootstrap}

    until nc -z ${aws_db_instance.main.address} 5432; do sleep 5; done
    until nc -z ${aws_instance.redis.private_ip} 6379; do sleep 5; done

    PGPASSWORD='Bite_Master_2024!' psql -h ${aws_db_instance.main.address} -U bite_master -d bite_master \
      -c "CREATE DATABASE cloud_db;" || true
    PGPASSWORD='Bite_Master_2024!' psql -h ${aws_db_instance.main.address} -U bite_master -d bite_master \
      -c "CREATE USER cloud_user WITH PASSWORD 'Cloud_2024!';" || true
    PGPASSWORD='Bite_Master_2024!' psql -h ${aws_db_instance.main.address} -U bite_master -d bite_master \
      -c "GRANT ALL PRIVILEGES ON DATABASE cloud_db TO cloud_user;" || true
    PGPASSWORD='Bite_Master_2024!' psql -h ${aws_db_instance.main.address} -U bite_master -d cloud_db \
      -c "GRANT ALL ON SCHEMA public TO cloud_user;" || true

    cd ${local.repo_dir}/manejador_cloud
    sudo python3 -m pip install -r requirements.txt
    python3 manage.py migrate --noinput || true
    # Seed ProveedorCloud, CuentaCloud, RecursoCloud, MetricaConsumo
    python3 manage.py seed_cloud_data || true
    nohup python3 manage.py runserver 0.0.0.0:8002 > /var/log/manejador_cloud.log 2>&1 &
  EOT

  tags = merge(local.common_tags, {
    Name    = "${var.project_prefix}-manejador-cloud"
    Role    = "app-server"
    Service = "cloud"
  })
}

# -----------------------------------------------------------------------------
# WORKER GOLANG POOL — ASR15 async event consumer (replaces Celery)
# Consumes bite.eventos from RabbitMQ and persists to reportes_db.
# -----------------------------------------------------------------------------

resource "aws_instance" "worker_pool" {
  for_each = toset(["a"])

  ami                         = data.aws_ami.ubuntu.id
  instance_type               = var.instance_type_worker
  subnet_id                   = element(tolist(data.aws_subnets.default.ids), 0)
  associate_public_ip_address = true
  vpc_security_group_ids      = [aws_security_group.worker.id, aws_security_group.ssh.id]


  root_block_device {
    volume_size = 20
    volume_type = "gp3"
  }

  depends_on = [
    aws_db_instance.main,
    aws_instance.rabbitmq,
  ]

  user_data = <<-EOT
    #!/bin/bash
    set -euxo pipefail
    export DEBIAN_FRONTEND=noninteractive

    sudo tee /etc/environment <<ENV
    DATABASE_HOST=${aws_db_instance.main.address}
    DATABASE_PORT=5432
    DATABASE_NAME=reportes_db
    DATABASE_USER=reportes_user
    DATABASE_PASSWORD=Reportes_2024!
    DATABASE_MAX_CONNS=20
    RABBITMQ_URL=amqp://bite:bite_pass@${aws_instance.rabbitmq.private_ip}:5672/bite_vhost
    WORKER_CONCURRENCY=${var.celery_worker_concurrency}
    PORT=8006
    SIMULATE_SLOW_PROCESSING=${var.simulate_slow_processing}
    ENV

    export DATABASE_HOST=${aws_db_instance.main.address}
    export DATABASE_PORT=5432
    export DATABASE_NAME=reportes_db
    export DATABASE_USER=reportes_user
    export DATABASE_PASSWORD='Reportes_2024!'
    export DATABASE_MAX_CONNS=20
    export RABBITMQ_URL=amqp://bite:bite_pass@${aws_instance.rabbitmq.private_ip}:5672/bite_vhost
    export WORKER_CONCURRENCY=${var.celery_worker_concurrency}
    export PORT=8006
    export SIMULATE_SLOW_PROCESSING=${var.simulate_slow_processing}

    ${local.git_bootstrap}

    until nc -z ${aws_db_instance.main.address} 5432; do sleep 5; done
    until nc -z ${aws_instance.rabbitmq.private_ip} 5672; do sleep 5; done

    ${local.worker_golang_bootstrap}

    cd ${local.repo_dir}/worker_golang
    nohup ./worker_golang > /var/log/worker_golang.log 2>&1 &
  EOT

  tags = merge(local.common_tags, {
    Name = "${var.project_prefix}-worker-${each.key}"
    Role = "worker-golang"
  })
}

# -----------------------------------------------------------------------------
# MANEJADOR_AUTENTICACION — port 8004 (unchanged EC2)
# CHANGE 4: REDIS_URL kept (db 0 = token cache; one of the two allowed users)
# -----------------------------------------------------------------------------

resource "aws_instance" "manejador_autenticacion" {
  ami                         = data.aws_ami.ubuntu.id
  instance_type               = var.instance_type_app
  subnet_id                   = element(tolist(data.aws_subnets.default.ids), 0)
  associate_public_ip_address = true
  vpc_security_group_ids      = [aws_security_group.auth.id]

  root_block_device {
    volume_size = 20
    volume_type = "gp3"
  }

  depends_on = [
    aws_db_instance.main,
    aws_instance.redis,
    aws_cognito_user_pool.bite,
  ]

  user_data = <<-EOT
    #!/bin/bash
    set -euxo pipefail
    export DEBIAN_FRONTEND=noninteractive

    sudo tee /etc/environment <<ENV
    DATABASE_HOST=${aws_db_instance.main.address}
    DATABASE_PORT=5432
    DATABASE_NAME=seguridad_db
    DATABASE_USER=seguridad_user
    DATABASE_PASSWORD=Seguridad_2024!
    REDIS_URL=redis://${aws_instance.redis.private_ip}:6379/0
    COGNITO_USER_POOL_ID=${aws_cognito_user_pool.bite.id}
    COGNITO_CLIENT_ID=${aws_cognito_user_pool_client.bite_spa.id}
    COGNITO_REGION=${var.region}
    LOCAL_JWT_SECRET=bite-local-jwt-secret
    ALLOWED_HOSTS=${local.django_allowed_hosts}
    DEBUG=True
    SECRET_KEY=bite-terraform-secret-key
    ENV

    export DATABASE_HOST=${aws_db_instance.main.address}
    export DATABASE_PORT=5432
    export DATABASE_NAME=seguridad_db
    export DATABASE_USER=seguridad_user
    export DATABASE_PASSWORD='Seguridad_2024!'
    export REDIS_URL=redis://${aws_instance.redis.private_ip}:6379/0
    export COGNITO_USER_POOL_ID=${aws_cognito_user_pool.bite.id}
    export COGNITO_CLIENT_ID=${aws_cognito_user_pool_client.bite_spa.id}
    export COGNITO_REGION=${var.region}
    export LOCAL_JWT_SECRET=bite-local-jwt-secret
    export ALLOWED_HOSTS=${local.django_allowed_hosts}
    export DEBUG=True
    export SECRET_KEY=bite-terraform-secret-key

    ${local.git_bootstrap}

    until nc -z ${aws_db_instance.main.address} 5432; do sleep 5; done
    until nc -z ${aws_instance.redis.private_ip} 6379; do sleep 5; done

    PGPASSWORD='Bite_Master_2024!' psql -h ${aws_db_instance.main.address} -U bite_master -d bite_master \
      -c "CREATE DATABASE seguridad_db;" || true
    PGPASSWORD='Bite_Master_2024!' psql -h ${aws_db_instance.main.address} -U bite_master -d bite_master \
      -c "CREATE USER seguridad_user WITH PASSWORD 'Seguridad_2024!';" || true
    PGPASSWORD='Bite_Master_2024!' psql -h ${aws_db_instance.main.address} -U bite_master -d bite_master \
      -c "GRANT ALL PRIVILEGES ON DATABASE seguridad_db TO seguridad_user;" || true
    PGPASSWORD='Bite_Master_2024!' psql -h ${aws_db_instance.main.address} -U bite_master -d seguridad_db \
      -c "GRANT ALL ON SCHEMA public TO seguridad_user;" || true

    cd ${local.repo_dir}/manejador_autenticacion
    sudo python3 -m pip install -r requirements.txt
    python3 manage.py migrate --noinput || true
    python3 manage.py seed_auth_users || true
    nohup python3 manage.py runserver 0.0.0.0:8004 > /var/log/manejador_autenticacion.log 2>&1 &
  EOT

  tags = merge(local.common_tags, {
    Name    = "${var.project_prefix}-manejador-autenticacion"
    Role    = "app-server"
    Service = "autenticacion"
  })
}

# -----------------------------------------------------------------------------
# MANEJADOR_SEGURIDAD — port 8005 (unchanged EC2)
# -----------------------------------------------------------------------------

resource "aws_instance" "manejador_seguridad" {
  ami                         = data.aws_ami.ubuntu.id
  instance_type               = var.instance_type_app
  subnet_id                   = element(tolist(data.aws_subnets.default.ids), 0)
  associate_public_ip_address = true
  vpc_security_group_ids      = [aws_security_group.auth.id]

  root_block_device {
    volume_size = 20
    volume_type = "gp3"
  }

  depends_on = [
    aws_db_instance.main,
    aws_instance.manejador_autenticacion,
  ]

  user_data = <<-EOT
    #!/bin/bash
    set -euxo pipefail
    export DEBIAN_FRONTEND=noninteractive

    sudo tee /etc/environment <<ENV
    DATABASE_HOST=${aws_db_instance.main.address}
    DATABASE_PORT=5432
    DATABASE_NAME=seguridad_db
    DATABASE_USER=seguridad_user
    DATABASE_PASSWORD=Seguridad_2024!
    AUTH_SERVICE_URL=http://${aws_lb.main.dns_name}
    AUTH_SERVICE_TIMEOUT=2
    LOCAL_JWT_SECRET=bite-local-jwt-secret
    COGNITO_USER_POOL_ID=${aws_cognito_user_pool.bite.id}
    COGNITO_CLIENT_ID=${aws_cognito_user_pool_client.bite_spa.id}
    COGNITO_REGION=${var.region}
    ALLOWED_HOSTS=${local.django_allowed_hosts}
    DEBUG=True
    SECRET_KEY=bite-terraform-secret-key
    ENV

    export DATABASE_HOST=${aws_db_instance.main.address}
    export DATABASE_PORT=5432
    export DATABASE_NAME=seguridad_db
    export DATABASE_USER=seguridad_user
    export DATABASE_PASSWORD='Seguridad_2024!'
    export AUTH_SERVICE_URL=http://${aws_lb.main.dns_name}
    export AUTH_SERVICE_TIMEOUT=2
    export LOCAL_JWT_SECRET=bite-local-jwt-secret
    export COGNITO_USER_POOL_ID=${aws_cognito_user_pool.bite.id}
    export COGNITO_CLIENT_ID=${aws_cognito_user_pool_client.bite_spa.id}
    export COGNITO_REGION=${var.region}
    export ALLOWED_HOSTS=${local.django_allowed_hosts}
    export DEBUG=True
    export SECRET_KEY=bite-terraform-secret-key

    ${local.git_bootstrap}

    until nc -z ${aws_db_instance.main.address} 5432; do sleep 5; done
    until nc -z ${aws_instance.manejador_autenticacion.private_ip} 8004; do sleep 5; done

    cd ${local.repo_dir}/manejador_seguridad
    sudo python3 -m pip install -r requirements.txt
    python3 manage.py migrate --noinput || true
    nohup python3 manage.py runserver 0.0.0.0:8005 > /var/log/manejador_seguridad.log 2>&1 &
  EOT

  tags = merge(local.common_tags, {
    Name    = "${var.project_prefix}-manejador-seguridad"
    Role    = "app-server"
    Service = "seguridad"
  })
}

# -----------------------------------------------------------------------------
# CHANGE 6 — SINGLE PUBLIC ALB (replaces API Gateway + two internal ALBs)
#
# One internet-facing ALB serves as the single entry point.
# Path-based listener rules route each prefix to its target group:
#   /auth/*      → TG autenticacion  :8004  (EC2, attached below)
#   /security/*  → TG seguridad      :8005  (EC2, attached below)
#   /cloud/*     → TG cloud          :8002  (EC2, attached below)
#   /projects/*  → TG usuarios       :8001  (ASG auto-registers)
#   /events/*    → TG reportes       :8003  (ASG auto-registers)
#   /reports/*   → TG reportes       :8003  (ASG auto-registers)
#
# The ALB lives inside the VPC so it can reach all private IPs directly —
# no VPC Link, no public-IP workaround needed.
# -----------------------------------------------------------------------------

resource "aws_lb" "main" {
  name               = "${var.project_prefix}-alb"
  internal           = false
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id]
  subnets            = tolist(data.aws_subnets.default.ids)

  tags = merge(local.common_tags, { Name = "${var.project_prefix}-alb" })
}

# Target group for manejador_usuarios - ASR16 latency experiment
# Kept from original config; ASG (CHANGE 2) registers instances automatically.
resource "aws_lb_target_group" "usuarios" {
  name     = "${var.project_prefix}-tg-usuarios"
  port     = 8001
  protocol = "HTTP"
  vpc_id   = data.aws_vpc.default.id

  health_check {
    path                = "/health"
    interval            = 30
    timeout             = 10
    healthy_threshold   = 2
    unhealthy_threshold = 3
    matcher             = "200"
  }

  tags = merge(local.common_tags, { Name = "${var.project_prefix}-tg-usuarios" })
}

# Target group for manejador_reportes - ASR17 scalability experiment
# Kept from original config; ASG (CHANGE 2) registers instances automatically.
resource "aws_lb_target_group" "reportes" {
  name     = "${var.project_prefix}-tg-reportes"
  port     = 8003
  protocol = "HTTP"
  vpc_id   = data.aws_vpc.default.id

  health_check {
    path                = "/health"
    interval            = 30
    timeout             = 10
    healthy_threshold   = 2
    unhealthy_threshold = 3
    matcher             = "200"
  }

  tags = merge(local.common_tags, { Name = "${var.project_prefix}-tg-reportes" })
}

# ── Target groups for the 3 fixed EC2 services (no ASG) ───────────────────

resource "aws_lb_target_group" "autenticacion" {
  name     = "${var.project_prefix}-tg-auth"
  port     = 8004
  protocol = "HTTP"
  vpc_id   = data.aws_vpc.default.id

  health_check {
    path                = "/health"
    interval            = 30
    timeout             = 10
    healthy_threshold   = 2
    unhealthy_threshold = 3
    matcher             = "200"
  }

  tags = merge(local.common_tags, { Name = "${var.project_prefix}-tg-auth" })
}

resource "aws_lb_target_group" "seguridad" {
  name     = "${var.project_prefix}-tg-security"
  port     = 8005
  protocol = "HTTP"
  vpc_id   = data.aws_vpc.default.id

  health_check {
    path                = "/health"
    interval            = 30
    timeout             = 10
    healthy_threshold   = 2
    unhealthy_threshold = 3
    matcher             = "200"
  }

  tags = merge(local.common_tags, { Name = "${var.project_prefix}-tg-security" })
}

resource "aws_lb_target_group" "cloud" {
  name     = "${var.project_prefix}-tg-cloud"
  port     = 8002
  protocol = "HTTP"
  vpc_id   = data.aws_vpc.default.id

  health_check {
    path                = "/health"
    interval            = 30
    timeout             = 10
    healthy_threshold   = 2
    unhealthy_threshold = 3
    matcher             = "200"
  }

  tags = merge(local.common_tags, { Name = "${var.project_prefix}-tg-cloud" })
}

# Attach the fixed EC2 instances to their target groups
resource "aws_lb_target_group_attachment" "autenticacion" {
  target_group_arn = aws_lb_target_group.autenticacion.arn
  target_id        = aws_instance.manejador_autenticacion.id
  port             = 8004
}

resource "aws_lb_target_group_attachment" "seguridad" {
  target_group_arn = aws_lb_target_group.seguridad.arn
  target_id        = aws_instance.manejador_seguridad.id
  port             = 8005
}

resource "aws_lb_target_group_attachment" "cloud" {
  target_group_arn = aws_lb_target_group.cloud.arn
  target_id        = aws_instance.manejador_cloud.id
  port             = 8002
}

# ── Main listener: port 80, default → 404 ─────────────────────────────────

resource "aws_lb_listener" "main" {
  load_balancer_arn = aws_lb.main.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type = "fixed-response"
    fixed_response {
      content_type = "text/plain"
      message_body = "Not found"
      status_code  = "404"
    }
  }
}

# ── Listener rules: path-based routing ────────────────────────────────────

resource "aws_lb_listener_rule" "auth" {
  listener_arn = aws_lb_listener.main.arn
  priority     = 10

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.autenticacion.arn
  }
  condition {
    path_pattern { values = ["/auth/*", "/auth"] }
  }
}

resource "aws_lb_listener_rule" "security" {
  listener_arn = aws_lb_listener.main.arn
  priority     = 20

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.seguridad.arn
  }
  condition {
    path_pattern { values = ["/security/*", "/security"] }
  }
}

resource "aws_lb_listener_rule" "cloud" {
  listener_arn = aws_lb_listener.main.arn
  priority     = 30

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.cloud.arn
  }
  condition {
    path_pattern { values = ["/cloud/*", "/cloud"] }
  }
}

resource "aws_lb_listener_rule" "projects" {
  listener_arn = aws_lb_listener.main.arn
  priority     = 40

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.usuarios.arn
  }
  condition {
    path_pattern { values = ["/projects/*", "/projects"] }
  }
}

resource "aws_lb_listener_rule" "events" {
  listener_arn = aws_lb_listener.main.arn
  priority     = 50

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.reportes.arn
  }
  condition {
    path_pattern { values = ["/events/*", "/events"] }
  }
}

resource "aws_lb_listener_rule" "reports" {
  listener_arn = aws_lb_listener.main.arn
  priority     = 60

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.reportes.arn
  }
  condition {
    path_pattern { values = ["/reports/*", "/reports"] }
  }
}

# -----------------------------------------------------------------------------
# CHANGE 2 — AUTO SCALING GROUPS for manejador_usuarios and manejador_reportes
# The fixed aws_instance.manejador_usuarios and aws_instance.manejador_reportes
# are replaced by launch templates + ASGs. ASGs self-register with the target
# groups above, so no aws_lb_target_group_attachment resources are needed.
# -----------------------------------------------------------------------------

# --- manejador_usuarios ASG ---

resource "aws_launch_template" "usuarios" {
  name_prefix   = "${var.project_prefix}-lt-usuarios-"
  image_id      = data.aws_ami.ubuntu.id
  instance_type = var.instance_type_app

  vpc_security_group_ids = [aws_security_group.app.id, aws_security_group.ssh.id]

  block_device_mappings {
    device_name = "/dev/sda1"
    ebs {
      volume_size = 20
      volume_type = "gp3"
    }
  }

  # CHANGE 4: REDIS_URL removed from usuarios entirely
  user_data = base64encode(<<-EOT
    #!/bin/bash
    set -euxo pipefail
    export DEBIAN_FRONTEND=noninteractive

    sudo tee /etc/environment <<ENV
    DATABASE_HOST=${aws_db_instance.main.address}
    DATABASE_PORT=5432
    DATABASE_NAME=usuarios_db
    DATABASE_USER=usuarios_user
    DATABASE_PASSWORD=Usuarios_2024!
    RABBITMQ_URL=amqp://bite:bite_pass@${aws_instance.rabbitmq.private_ip}:5672/bite_vhost
    RESOURCE_SERVICE_URL=http://${aws_instance.manejador_cloud.private_ip}:8002
    AUTH_SERVICE_URL=http://${aws_lb.main.dns_name}
    AUTH_SERVICE_TIMEOUT=10
    ALLOWED_HOSTS=${local.django_allowed_hosts}
    DEBUG=True
    SECRET_KEY=bite-terraform-secret-key
    EMAIL_BACKEND=django.core.mail.backends.console.EmailBackend
    ENV

    export DATABASE_HOST=${aws_db_instance.main.address}
    export DATABASE_PORT=5432
    export DATABASE_NAME=usuarios_db
    export DATABASE_USER=usuarios_user
    export DATABASE_PASSWORD='Usuarios_2024!'
    export RABBITMQ_URL=amqp://bite:bite_pass@${aws_instance.rabbitmq.private_ip}:5672/bite_vhost
    export RESOURCE_SERVICE_URL=http://${aws_instance.manejador_cloud.private_ip}:8002
    export AUTH_SERVICE_URL=http://${aws_lb.main.dns_name}
    export AUTH_SERVICE_TIMEOUT=10
    export ALLOWED_HOSTS=${local.django_allowed_hosts}
    export DEBUG=True
    export SECRET_KEY=bite-terraform-secret-key

    ${local.git_bootstrap}

    until nc -z ${aws_db_instance.main.address} 5432; do sleep 5; done
    until nc -z ${aws_instance.rabbitmq.private_ip} 5672; do sleep 5; done
    until nc -z ${aws_instance.manejador_cloud.private_ip} 8002; do sleep 5; done
    until nc -z ${aws_instance.manejador_autenticacion.private_ip} 8004; do sleep 5; done

    PGPASSWORD='Bite_Master_2024!' psql -h ${aws_db_instance.main.address} -U bite_master -d bite_master \
      -c "CREATE DATABASE usuarios_db;" || true
    PGPASSWORD='Bite_Master_2024!' psql -h ${aws_db_instance.main.address} -U bite_master -d bite_master \
      -c "CREATE USER usuarios_user WITH PASSWORD 'Usuarios_2024!';" || true
    PGPASSWORD='Bite_Master_2024!' psql -h ${aws_db_instance.main.address} -U bite_master -d bite_master \
      -c "GRANT ALL PRIVILEGES ON DATABASE usuarios_db TO usuarios_user;" || true
    PGPASSWORD='Bite_Master_2024!' psql -h ${aws_db_instance.main.address} -U bite_master -d usuarios_db \
      -c "GRANT ALL ON SCHEMA public TO usuarios_user;" || true

    cd ${local.repo_dir}/manejador_usuarios
    sudo python3 -m pip install -r requirements.txt
    python3 manage.py migrate --noinput || true
    python3 manage.py seed_usuarios_data || true
    nohup python3 manage.py runserver 0.0.0.0:8001 > /var/log/manejador_usuarios.log 2>&1 &
  EOT
  )

  tags = merge(local.common_tags, {
    Name    = "${var.project_prefix}-lt-usuarios"
    Service = "usuarios"
  })
}

resource "aws_autoscaling_group" "usuarios" {
  name                = "${var.project_prefix}-asg-usuarios"
  min_size            = 1
  max_size            = 4
  desired_capacity    = 1
  vpc_zone_identifier = tolist(data.aws_subnets.default.ids)
  target_group_arns   = [aws_lb_target_group.usuarios.arn]

  launch_template {
    id      = aws_launch_template.usuarios.id
    version = "$Latest"
  }

  health_check_type         = "ELB"
  health_check_grace_period = 300

  depends_on = [
    aws_db_instance.main,
    aws_instance.rabbitmq,
    aws_instance.manejador_cloud,
    aws_instance.manejador_autenticacion,
  ]

  tag {
    key                 = "Name"
    value               = "${var.project_prefix}-manejador-usuarios"
    propagate_at_launch = true
  }
  tag {
    key                 = "Service"
    value               = "usuarios"
    propagate_at_launch = true
  }
}

resource "aws_autoscaling_policy" "usuarios_scale_out" {
  name                   = "${var.project_prefix}-usuarios-scale-out"
  autoscaling_group_name = aws_autoscaling_group.usuarios.name
  adjustment_type        = "ChangeInCapacity"
  scaling_adjustment     = 1
  cooldown               = 120
  policy_type            = "SimpleScaling"
}

resource "aws_autoscaling_policy" "usuarios_scale_in" {
  name                   = "${var.project_prefix}-usuarios-scale-in"
  autoscaling_group_name = aws_autoscaling_group.usuarios.name
  adjustment_type        = "ChangeInCapacity"
  scaling_adjustment     = -1
  cooldown               = 300
  policy_type            = "SimpleScaling"
}

resource "aws_cloudwatch_metric_alarm" "usuarios_cpu_high" {
  alarm_name          = "${var.project_prefix}-usuarios-cpu-high"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "CPUUtilization"
  namespace           = "AWS/EC2"
  period              = 60
  statistic           = "Average"
  threshold           = 70
  alarm_description   = "Scale out manejador_usuarios when CPU > 70% for 2 periods"
  alarm_actions       = [aws_autoscaling_policy.usuarios_scale_out.arn]

  dimensions = {
    AutoScalingGroupName = aws_autoscaling_group.usuarios.name
  }

  tags = merge(local.common_tags, { Name = "${var.project_prefix}-usuarios-cpu-high" })
}

resource "aws_cloudwatch_metric_alarm" "usuarios_cpu_low" {
  alarm_name          = "${var.project_prefix}-usuarios-cpu-low"
  comparison_operator = "LessThanThreshold"
  evaluation_periods  = 5
  metric_name         = "CPUUtilization"
  namespace           = "AWS/EC2"
  period              = 60
  statistic           = "Average"
  threshold           = 30
  alarm_description   = "Scale in manejador_usuarios when CPU < 30% for 5 periods"
  alarm_actions       = [aws_autoscaling_policy.usuarios_scale_in.arn]

  dimensions = {
    AutoScalingGroupName = aws_autoscaling_group.usuarios.name
  }

  tags = merge(local.common_tags, { Name = "${var.project_prefix}-usuarios-cpu-low" })
}

# --- manejador_reportes ASG ---

resource "aws_launch_template" "reportes" {
  name_prefix   = "${var.project_prefix}-lt-reportes-"
  image_id      = data.aws_ami.ubuntu.id
  instance_type = var.instance_type_app

  vpc_security_group_ids = [aws_security_group.app.id, aws_security_group.ssh.id]

  block_device_mappings {
    device_name = "/dev/sda1"
    ebs {
      volume_size = 20
      volume_type = "gp3"
    }
  }

  # ASR15: pika async publisher + worker_golang consumer (Celery removed)
  user_data = base64encode(<<-EOT
    #!/bin/bash
    set -euxo pipefail
    export DEBIAN_FRONTEND=noninteractive

    sudo tee /etc/environment <<ENV
    DATABASE_HOST=${aws_db_instance.main.address}
    DATABASE_PORT=5432
    DATABASE_NAME=reportes_db
    DATABASE_USER=reportes_user
    DATABASE_PASSWORD=Reportes_2024!
    RABBITMQ_URL=amqp://bite:bite_pass@${aws_instance.rabbitmq.private_ip}:5672/bite_vhost
    RABBITMQ_EXCHANGE=bite_events
    AUTH_SERVICE_URL=http://${aws_lb.main.dns_name}
    AUTH_SERVICE_TIMEOUT=2
    LOCAL_JWT_SECRET=bite-local-jwt-secret
    COGNITO_USER_POOL_ID=${aws_cognito_user_pool.bite.id}
    COGNITO_CLIENT_ID=${aws_cognito_user_pool_client.bite_spa.id}
    COGNITO_REGION=${var.region}
    ALLOWED_HOSTS=${local.django_allowed_hosts}
    DEBUG=True
    SECRET_KEY=bite-terraform-secret-key
    EMAIL_BACKEND=django.core.mail.backends.console.EmailBackend
    GUNICORN_WORKERS=2
    GUNICORN_THREADS=2
    GUNICORN_TIMEOUT=30
    ENV

    export DATABASE_HOST=${aws_db_instance.main.address}
    export DATABASE_PORT=5432
    export DATABASE_NAME=reportes_db
    export DATABASE_USER=reportes_user
    export DATABASE_PASSWORD='Reportes_2024!'
    export RABBITMQ_URL=amqp://bite:bite_pass@${aws_instance.rabbitmq.private_ip}:5672/bite_vhost
    export RABBITMQ_EXCHANGE=bite_events
    export AUTH_SERVICE_URL=http://${aws_lb.main.dns_name}
    export AUTH_SERVICE_TIMEOUT=2
    export LOCAL_JWT_SECRET=bite-local-jwt-secret
    export COGNITO_USER_POOL_ID=${aws_cognito_user_pool.bite.id}
    export COGNITO_CLIENT_ID=${aws_cognito_user_pool_client.bite_spa.id}
    export COGNITO_REGION=${var.region}
    export ALLOWED_HOSTS=${local.django_allowed_hosts}
    export DEBUG=True
    export SECRET_KEY=bite-terraform-secret-key

    ${local.git_bootstrap}

    until nc -z ${aws_db_instance.main.address} 5432; do sleep 5; done
    until nc -z ${aws_instance.rabbitmq.private_ip} 5672; do sleep 5; done
    until nc -z ${aws_instance.manejador_autenticacion.private_ip} 8004; do sleep 5; done

    PGPASSWORD='Bite_Master_2024!' psql -h ${aws_db_instance.main.address} -U bite_master -d bite_master \
      -c "CREATE DATABASE reportes_db;" || true
    PGPASSWORD='Bite_Master_2024!' psql -h ${aws_db_instance.main.address} -U bite_master -d bite_master \
      -c "CREATE USER reportes_user WITH PASSWORD 'Reportes_2024!';" || true
    PGPASSWORD='Bite_Master_2024!' psql -h ${aws_db_instance.main.address} -U bite_master -d bite_master \
      -c "GRANT ALL PRIVILEGES ON DATABASE reportes_db TO reportes_user;" || true
    PGPASSWORD='Bite_Master_2024!' psql -h ${aws_db_instance.main.address} -U bite_master -d reportes_db \
      -c "GRANT ALL ON SCHEMA public TO reportes_user;" || true

    cd ${local.repo_dir}/manejador_reportes
    sudo python3 -m pip install -r requirements.txt
    python3 manage.py migrate --noinput || true
    python3 manage.py seed_reportes_data || true
    nohup gunicorn manejador_reportes.wsgi:application \
      --bind 0.0.0.0:8003 \
      --workers 2 \
      --threads 2 \
      --timeout 30 \
      --access-logfile - \
      --error-logfile - \
      > /var/log/manejador_reportes.log 2>&1 &
  EOT
  )

  tags = merge(local.common_tags, {
    Name    = "${var.project_prefix}-lt-reportes"
    Service = "reportes"
  })
}

resource "aws_autoscaling_group" "reportes" {
  name                = "${var.project_prefix}-asg-reportes"
  min_size            = 1
  max_size            = 6
  desired_capacity    = 2
  vpc_zone_identifier = tolist(data.aws_subnets.default.ids)
  target_group_arns   = [aws_lb_target_group.reportes.arn]

  launch_template {
    id      = aws_launch_template.reportes.id
    version = "$Latest"
  }

  health_check_type         = "ELB"
  health_check_grace_period = 300

  depends_on = [
    aws_db_instance.main,
    aws_instance.rabbitmq,
  ]

  tag {
    key                 = "Name"
    value               = "${var.project_prefix}-manejador-reportes"
    propagate_at_launch = true
  }
  tag {
    key                 = "Service"
    value               = "reportes"
    propagate_at_launch = true
  }
}

resource "aws_autoscaling_policy" "reportes_scale_out" {
  name                   = "${var.project_prefix}-reportes-scale-out"
  autoscaling_group_name = aws_autoscaling_group.reportes.name
  adjustment_type        = "ChangeInCapacity"
  scaling_adjustment     = 1
  cooldown               = 120
  policy_type            = "SimpleScaling"
}

resource "aws_autoscaling_policy" "reportes_scale_in" {
  name                   = "${var.project_prefix}-reportes-scale-in"
  autoscaling_group_name = aws_autoscaling_group.reportes.name
  adjustment_type        = "ChangeInCapacity"
  scaling_adjustment     = -1
  cooldown               = 300
  policy_type            = "SimpleScaling"
}

resource "aws_cloudwatch_metric_alarm" "reportes_cpu_high" {
  alarm_name          = "${var.project_prefix}-reportes-cpu-high"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "CPUUtilization"
  namespace           = "AWS/EC2"
  period              = 60
  statistic           = "Average"
  threshold           = 70
  alarm_description   = "Scale out manejador_reportes when CPU > 70% for 2 periods"
  alarm_actions       = [aws_autoscaling_policy.reportes_scale_out.arn]

  dimensions = {
    AutoScalingGroupName = aws_autoscaling_group.reportes.name
  }

  tags = merge(local.common_tags, { Name = "${var.project_prefix}-reportes-cpu-high" })
}

resource "aws_cloudwatch_metric_alarm" "reportes_cpu_low" {
  alarm_name          = "${var.project_prefix}-reportes-cpu-low"
  comparison_operator = "LessThanThreshold"
  evaluation_periods  = 5
  metric_name         = "CPUUtilization"
  namespace           = "AWS/EC2"
  period              = 60
  statistic           = "Average"
  threshold           = 30
  alarm_description   = "Scale in manejador_reportes when CPU < 30% for 5 periods"
  alarm_actions       = [aws_autoscaling_policy.reportes_scale_in.arn]

  dimensions = {
    AutoScalingGroupName = aws_autoscaling_group.reportes.name
  }

  tags = merge(local.common_tags, { Name = "${var.project_prefix}-reportes-cpu-low" })
}

# -----------------------------------------------------------------------------
# CHANGE 6 — API Gateway removed.
# Routing is now handled by aws_lb.main (public ALB) with path-based rules.
# See the ALB + listener rules section above.
# -----------------------------------------------------------------------------

# -----------------------------------------------------------------------------
# COGNITO USER POOL (ASR3 – Tenant Identity) — UNCHANGED
# Custom attribute custom:empresa_id stores the tenant UUID
# -----------------------------------------------------------------------------

resource "aws_cognito_user_pool" "bite" {
  name = "${var.project_prefix}-user-pool"

  username_attributes      = ["email"]
  auto_verified_attributes = ["email"]

  password_policy {
    minimum_length    = 8
    require_uppercase = true
    require_lowercase = true
    require_numbers   = true
    require_symbols   = false
  }

  schema {
    attribute_data_type = "String"
    name                = "empresa_id"
    mutable             = true
    string_attribute_constraints {
      min_length = 36
      max_length = 36
    }
  }

  schema {
    attribute_data_type = "String"
    name                = "rol"
    mutable             = true
    string_attribute_constraints {
      min_length = 4
      max_length = 10
    }
  }

  tags = merge(local.common_tags, { Name = "${var.project_prefix}-user-pool" })
}

resource "aws_cognito_user_pool_client" "bite_spa" {
  name         = "${var.project_prefix}-spa-client"
  user_pool_id = aws_cognito_user_pool.bite.id

  # No client secret — SPA-compatible
  generate_secret = false

  explicit_auth_flows = [
    "ALLOW_USER_PASSWORD_AUTH",
    "ALLOW_REFRESH_TOKEN_AUTH",
    "ALLOW_USER_SRP_AUTH",
  ]

  read_attributes = [
    "email",
    "email_verified",
    "custom:empresa_id",
    "custom:rol",
  ]

  write_attributes = [
    "email",
    "custom:empresa_id",
    "custom:rol",
  ]
}

# -----------------------------------------------------------------------------
# COGNITO TEST USERS — provisioned automatically so no manual CLI steps are
# needed after terraform apply.
#
# Both users are created with message_action = "SUPPRESS" (no welcome e-mail)
# and immediately confirmed to CONFIRMED status via a local-exec provisioner
# (admin-set-user-password --permanent) so no FORCE_CHANGE_PASSWORD challenge
# fires on first login.
#
# lifecycle { ignore_changes = [temporary_password] } prevents Terraform from
# resetting the password on every subsequent apply.
# -----------------------------------------------------------------------------

resource "aws_cognito_user" "empresa_a" {
  user_pool_id = aws_cognito_user_pool.bite.id
  username     = "empresa_a@bite.co"

  attributes = {
    email               = "empresa_a@bite.co"
    email_verified      = "true"
    "custom:empresa_id" = "550e8400-e29b-41d4-a716-446655440001"
    "custom:rol"        = "admin"
  }

  temporary_password = "BiteCo2024!"
  message_action     = "SUPPRESS" # do not send welcome email (fails in Academy)

  lifecycle {
    ignore_changes = [temporary_password] # do not reset password on re-apply
  }
}

resource "aws_cognito_user" "empresa_b" {
  user_pool_id = aws_cognito_user_pool.bite.id
  username     = "empresa_b@bite.co"

  attributes = {
    email               = "empresa_b@bite.co"
    email_verified      = "true"
    "custom:empresa_id" = "550e8400-e29b-41d4-a716-446655440002"
    "custom:rol"        = "admin"
  }

  temporary_password = "BiteCo2024!"
  message_action     = "SUPPRESS"

  lifecycle {
    ignore_changes = [temporary_password]
  }
}

# Promote empresa_a from FORCE_CHANGE_PASSWORD → CONFIRMED immediately.
resource "null_resource" "confirm_empresa_a" {
  depends_on = [aws_cognito_user.empresa_a]

  provisioner "local-exec" {
    command = <<-EOT
      aws cognito-idp admin-set-user-password \
        --user-pool-id ${aws_cognito_user_pool.bite.id} \
        --username empresa_a@bite.co \
        --password "BiteCo2024!" \
        --permanent \
        --region ${var.region}
    EOT
  }
}

# Promote empresa_b from FORCE_CHANGE_PASSWORD → CONFIRMED immediately.
resource "null_resource" "confirm_empresa_b" {
  depends_on = [aws_cognito_user.empresa_b]

  provisioner "local-exec" {
    command = <<-EOT
      aws cognito-idp admin-set-user-password \
        --user-pool-id ${aws_cognito_user_pool.bite.id} \
        --username empresa_b@bite.co \
        --password "BiteCo2024!" \
        --permanent \
        --region ${var.region}
    EOT
  }
}

# Re-sync custom attributes so id_token includes custom:empresa_id after client read_attributes change.
resource "null_resource" "sync_cognito_user_attrs" {
  depends_on = [
    null_resource.confirm_empresa_a,
    null_resource.confirm_empresa_b,
    aws_cognito_user_pool_client.bite_spa,
  ]

  triggers = {
    pool_id   = aws_cognito_user_pool.bite.id
    client_id = aws_cognito_user_pool_client.bite_spa.id
  }

  provisioner "local-exec" {
    command = <<-EOT
      aws cognito-idp admin-update-user-attributes \
        --user-pool-id ${aws_cognito_user_pool.bite.id} \
        --username empresa_a@bite.co \
        --user-attributes Name=custom:empresa_id,Value=550e8400-e29b-41d4-a716-446655440001 Name=custom:rol,Value=admin \
        --region ${var.region} || true
      aws cognito-idp admin-update-user-attributes \
        --user-pool-id ${aws_cognito_user_pool.bite.id} \
        --username empresa_b@bite.co \
        --user-attributes Name=custom:empresa_id,Value=550e8400-e29b-41d4-a716-446655440002 Name=custom:rol,Value=admin \
        --region ${var.region} || true
    EOT
  }
}

# -----------------------------------------------------------------------------
# S3 FRONTEND BUCKET — static HTML/CSS/JS site
# CHANGE 6: config.js points to the public ALB DNS (HTTP).
#           config.js.tpl uses ${alb_dns} → rendered to http://<alb-dns>.
# -----------------------------------------------------------------------------

resource "aws_s3_bucket" "frontend" {
  bucket = "${var.project_prefix}-frontend-${data.aws_vpc.default.id}"
  tags   = merge(local.common_tags, { Name = "${var.project_prefix}-frontend" })
}

resource "aws_s3_bucket_website_configuration" "frontend" {
  bucket = aws_s3_bucket.frontend.id
  index_document { suffix = "index.html" }
  error_document { key = "index.html" }
}

resource "aws_s3_bucket_public_access_block" "frontend" {
  bucket                  = aws_s3_bucket.frontend.id
  block_public_acls       = false
  block_public_policy     = false
  ignore_public_acls      = false
  restrict_public_buckets = false
}

resource "aws_s3_bucket_policy" "frontend_public" {
  bucket = aws_s3_bucket.frontend.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = "*"
      Action    = "s3:GetObject"
      Resource  = "${aws_s3_bucket.frontend.arn}/*"
    }]
  })
  depends_on = [aws_s3_bucket_public_access_block.frontend]
}

# CHANGE 6: variable changed from api_gw_url → alb_dns (public ALB DNS name)
resource "aws_s3_object" "frontend_config" {
  bucket       = aws_s3_bucket.frontend.id
  key          = "config.js"
  content_type = "application/javascript"
  content = templatefile("${path.module}/frontend/config.js.tpl", {
    alb_dns = aws_lb.main.dns_name
  })
  depends_on = [
    aws_s3_bucket_public_access_block.frontend,
    aws_s3_bucket_policy.frontend_public,
  ]
}

resource "aws_s3_object" "frontend_index" {
  bucket       = aws_s3_bucket.frontend.id
  key          = "index.html"
  source       = "${path.module}/frontend/index.html"
  content_type = "text/html"
  etag         = filemd5("${path.module}/frontend/index.html")
  depends_on   = [aws_s3_bucket_public_access_block.frontend, aws_s3_bucket_policy.frontend_public]
}

resource "aws_s3_object" "frontend_dashboard" {
  bucket       = aws_s3_bucket.frontend.id
  key          = "dashboard.html"
  source       = "${path.module}/frontend/dashboard.html"
  content_type = "text/html"
  etag         = filemd5("${path.module}/frontend/dashboard.html")
  depends_on   = [aws_s3_bucket_public_access_block.frontend, aws_s3_bucket_policy.frontend_public]
}

resource "aws_s3_object" "frontend_metrics" {
  bucket       = aws_s3_bucket.frontend.id
  key          = "metrics.html"
  source       = "${path.module}/frontend/metrics.html"
  content_type = "text/html"
  etag         = filemd5("${path.module}/frontend/metrics.html")
  depends_on   = [aws_s3_bucket_public_access_block.frontend, aws_s3_bucket_policy.frontend_public]
}

resource "aws_s3_object" "frontend_reports" {
  bucket       = aws_s3_bucket.frontend.id
  key          = "reports.html"
  source       = "${path.module}/frontend/reports.html"
  content_type = "text/html"
  etag         = filemd5("${path.module}/frontend/reports.html")
  depends_on   = [aws_s3_bucket_public_access_block.frontend, aws_s3_bucket_policy.frontend_public]
}

resource "aws_s3_object" "frontend_users" {
  bucket       = aws_s3_bucket.frontend.id
  key          = "users.html"
  source       = "${path.module}/frontend/users.html"
  content_type = "text/html"
  etag         = filemd5("${path.module}/frontend/users.html")
  depends_on   = [aws_s3_bucket_public_access_block.frontend, aws_s3_bucket_policy.frontend_public]
}

# -----------------------------------------------------------------------------
# CHANGE 5 — LAMBDA + EVENTBRIDGE (cloud_collector)
# Runs every 6 hours to collect AWS Cost Explorer data into cloud_db (primary).
# Writes directly to DATABASE_HOST (primary RDS); reads on the service side go
# through DATABASE_READ_HOST (read replica).
#
# Prerequisites:
#   1. Create cloud_collector/ directory at the repo root with handler.py
#      (function signature: lambda_handler(event, context))
#   2. `terraform init` to pull hashicorp/archive provider
# -----------------------------------------------------------------------------

data "archive_file" "cloud_collector" {
  type        = "zip"
  source_dir  = "${path.module}/cloud_collector"
  output_path = "${path.module}/cloud_collector.zip"
}

# AWS Academy uses a pre-existing IAM role (voclabs/LabRole) — we cannot create
# new roles. Use a data source to reference the existing LabRole.
data "aws_iam_role" "lab_role" {
  name = "LabRole"
}

resource "aws_lambda_function" "cloud_collector" {
  filename      = data.archive_file.cloud_collector.output_path
  function_name = "${var.project_prefix}-cloud-collector"
  runtime       = "python3.11"
  handler       = "handler.lambda_handler"
  timeout       = 300
  memory_size   = 256

  source_code_hash = data.archive_file.cloud_collector.output_base64sha256

  environment {
    variables = {
      DATABASE_HOST     = aws_db_instance.main.address
      DATABASE_NAME     = "cloud_db"
      DATABASE_USER     = "cloud_user"
      DATABASE_PASSWORD = "Cloud_2024!"
      REDIS_URL         = "redis://${aws_instance.redis.private_ip}:6379/1"
    }
  }

  vpc_config {
    subnet_ids         = tolist(data.aws_subnets.default.ids)
    security_group_ids = [aws_security_group.lambda.id]
  }

  # Use the pre-existing AWS Academy LabRole instead of creating a new one
  role = data.aws_iam_role.lab_role.arn

  tags = merge(local.common_tags, {
    Name    = "${var.project_prefix}-cloud-collector"
    Service = "cloud"
  })
}

resource "aws_cloudwatch_event_rule" "cloud_collector_schedule" {
  name                = "${var.project_prefix}-cloud-collector-schedule"
  description         = "Trigger cloud_collector Lambda every 6 hours"
  schedule_expression = "rate(6 hours)"

  tags = merge(local.common_tags, { Name = "${var.project_prefix}-cloud-collector-schedule" })
}

resource "aws_cloudwatch_event_target" "cloud_collector_target" {
  rule = aws_cloudwatch_event_rule.cloud_collector_schedule.name
  arn  = aws_lambda_function.cloud_collector.arn
}

resource "aws_lambda_permission" "allow_eventbridge" {
  statement_id  = "AllowExecutionFromEventBridge"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.cloud_collector.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.cloud_collector_schedule.arn
}

# NOTE: aws_iam_role and aws_iam_role_policy removed — AWS Academy does not allow
# iam:CreateRole. The Lambda now uses the pre-existing LabRole (see data source above).

# -----------------------------------------------------------------------------
# OUTPUTS - use these in JMeter HTTP Request samplers and for debugging
# Every original output is preserved; values updated where aws_lb.main was used.
# New outputs: api_gateway_invoke_url, alb_usuarios_dns, alb_reportes_dns,
#              cloud_read_replica_endpoint, rds_primary_endpoint
# -----------------------------------------------------------------------------

output "alb_dns_name" {
  description = "Public ALB DNS — base URL for all frontend and JMeter calls (CHANGE 6)"
  value       = "http://${aws_lb.main.dns_name}"
}

output "alb_usuarios_url" {
  description = "ASR16 latency experiment endpoint via public ALB"
  value       = "http://${aws_lb.main.dns_name}/projects"
}

output "alb_reportes_url" {
  description = "ASR17 scalability experiment endpoint via public ALB"
  value       = "http://${aws_lb.main.dns_name}/events/batch"
}

output "manejador_cloud_public_ip" {
  description = "manejador_cloud public IP - internal service, for SSH debugging only"
  value       = aws_instance.manejador_cloud.public_ip
}

output "redis_private_ip" {
  description = "Redis private IP - VPC-internal only"
  value       = aws_instance.redis.private_ip
}

output "rabbitmq_private_ip" {
  description = "RabbitMQ private IP - VPC-internal only"
  value       = aws_instance.rabbitmq.private_ip
}

output "rabbitmq_management_url" {
  description = "RabbitMQ management UI - accessible from within the VPC only"
  value       = "http://${aws_instance.rabbitmq.private_ip}:15672"
}

output "rds_endpoint" {
  description = "RDS PostgreSQL primary endpoint — all microservices connect here"
  value       = aws_db_instance.main.address
}

output "rds_primary_endpoint" {
  description = "RDS PostgreSQL primary endpoint (write path) — CHANGE 3"
  value       = aws_db_instance.main.address
}

output "cloud_read_replica_endpoint" {
  description = "RDS read replica endpoint for cloud_db reads (CQRS) — CHANGE 3"
  value       = aws_db_instance.cloud_read_replica.address
}

output "worker_public_ips" {
  description = "worker_golang pool public IPs - for SSH debugging"
  value       = { for id, instance in aws_instance.worker_pool : id => instance.public_ip }
}

output "simulate_slow_processing" {
  description = "ASR15 demo flag — set to true to enable 3s sleep in worker_golang"
  value       = var.simulate_slow_processing
}

output "cognito_user_pool_id" {
  description = "Cognito User Pool ID — set as COGNITO_USER_POOL_ID env var on app servers"
  value       = aws_cognito_user_pool.bite.id
}

output "cognito_client_id" {
  description = "Cognito App Client ID — set as COGNITO_CLIENT_ID env var on app servers"
  value       = aws_cognito_user_pool_client.bite_spa.id
}

output "frontend_s3_url" {
  description = "S3 static website URL for the BITE.co frontend"
  value       = "http://${aws_s3_bucket.frontend.bucket}.s3-website-${var.region}.amazonaws.com"
}

output "manejador_autenticacion_public_ip" {
  description = "manejador_autenticacion public IP — SSH debugging"
  value       = aws_instance.manejador_autenticacion.public_ip
}

output "manejador_seguridad_public_ip" {
  description = "manejador_seguridad public IP — SSH debugging"
  value       = aws_instance.manejador_seguridad.public_ip
}

output "alb_auth_url" {
  description = "ASR2/ASR3 auth endpoint via public ALB (CHANGE 6)"
  value       = "http://${aws_lb.main.dns_name}/auth/login"
}

output "asr2_tls_status_url_http" {
  description = "ASR2 experiment: security/tls-status via public ALB (CHANGE 6)"
  value       = "http://${aws_lb.main.dns_name}/security/tls-status"
}

output "asr2_tls_status_url_https" {
  description = "ASR2 experiment: security/tls-status via public ALB (CHANGE 6)"
  value       = "http://${aws_lb.main.dns_name}/security/tls-status"
}

output "asr2_integrity_check_url" {
  description = "ASR2 experiment: HMAC integrity check endpoint via public ALB (CHANGE 6)"
  value       = "http://${aws_lb.main.dns_name}/security/integrity-check"
}

output "asr2_integrity_log_url" {
  description = "ASR2 experiment: audit log via public ALB (CHANGE 6)"
  value       = "http://${aws_lb.main.dns_name}/security/integrity-log"
}

output "cognito_test_users" {
  description = "Test users provisioned in Cognito — use for login verification after apply"
  value = {
    empresa_a = {
      username   = "empresa_a@bite.co"
      empresa_id = "550e8400-e29b-41d4-a716-446655440001"
      password   = "BiteCo2024!"
    }
    empresa_b = {
      username   = "empresa_b@bite.co"
      empresa_id = "550e8400-e29b-41d4-a716-446655440002"
      password   = "BiteCo2024!"
    }
  }
  sensitive = false
}

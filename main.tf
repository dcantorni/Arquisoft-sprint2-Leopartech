# =============================================================================
# BITE.co Cloud Cost Management Platform
# Terraform deployment for AWS Academy
#
# Architecture reference: architecture.md §3 Deployment Architecture
# Experiments:
#   - ASR16 (Latencia)    → ALB → manejador_usuarios → POST /projects
#   - ASR17 (Escalabilidad) → ALB → manejador_reportes → POST /events/batch
#                              → RabbitMQ → Worker Pool (Celery)
#
# Instance sizing: t3.micro / t3.small (cheapest viable for AWS Academy)
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
  description = "Number of concurrent Celery worker processes per worker instance (ASR17)"
  type        = number
  default     = 4
}

variable "key_name" {
  description = "EC2 key pair name for SSH access to instances in the Auto Scaling Group"
  type        = string
  default     = ""
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

# ASR2 – TLS provider for generating self-signed certificate
provider "tls" {}


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

  # Shared startup script: clones the repo and waits for dependencies
  # Usage: interpolate after setting env vars in each user_data block
  git_bootstrap = <<-SCRIPT
    sudo apt-get update -y
    sudo apt-get install -y --fix-missing python3-pip git build-essential libpq-dev python3-dev postgresql-client netcat-openbsd
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

# ALB security group - accepts HTTP and HTTPS from anywhere
# ASR2: port 443 required for TLS experiment evidence
resource "aws_security_group" "alb" {
  name        = "${var.project_prefix}-alb"
  description = "Application Load Balancer - public HTTP and HTTPS ingress"
  vpc_id      = data.aws_vpc.default.id

  ingress {
    description = "HTTP from Internet"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  # ASR2 – Integridad: HTTPS/TLS ingress
  ingress {
    description = "HTTPS/TLS from Internet (ASR2 integrity experiment)"
    from_port   = 443
    to_port     = 443
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

# App servers - only accept traffic from the ALB and SSH
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
    description     = "manejador_cloud from ALB and VPC (ASG + internal callers)"
    from_port       = 8002
    to_port         = 8002
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
  }

  ingress {
    description = "manejador_cloud internal VPC callers"
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

# -----------------------------------------------------------------------------
# SHARED INFRASTRUCTURE
# architecture.md §3.5 - Redis (Elasticache) + RabbitMQ (AMQP)
# Using EC2 for AWS Academy compatibility (Elasticache requires VPC config)
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
  skip_final_snapshot      = true
  publicly_accessible      = false
  deletion_protection      = false
  backup_retention_period  = 1   # required for read replica creation

  tags = merge(local.common_tags, {
    Name = "${var.project_prefix}-postgres"
    Role = "database"
  })
}

resource "aws_db_instance" "cloud_read_replica" {
  identifier             = "bite2-cloud-read-replica"
  replicate_source_db    = aws_db_instance.main.identifier
  instance_class         = "db.t3.micro"
  publicly_accessible    = false
  skip_final_snapshot    = true
  vpc_security_group_ids = [aws_security_group.db.id]

  tags = merge(local.common_tags, {
    Name = "bite2-cloud-read-replica"
    Role = "read-replica"
  })
}

# -----------------------------------------------------------------------------
# APPLICATION SERVERS
# architecture.md §3.3 - Django services on Ubuntu 22.04
# -----------------------------------------------------------------------------

# -----------------------------------------------------------------------------
# manejador_usuarios — Django + Auto Scaling Group (ASR16 latency support)
# ASG allows ALB to distribute POST /projects across multiple instances,
# directly supporting the P95 ≤ 500ms latency target under 150 concurrent threads.
# -----------------------------------------------------------------------------

resource "aws_launch_template" "usuarios" {
  name_prefix   = "${var.project_prefix}-usuarios-"
  image_id      = data.aws_ami.ubuntu.id
  instance_type = var.instance_type_app
  key_name      = var.key_name

  vpc_security_group_ids = [
    aws_security_group.app.id,
    aws_security_group.ssh.id,
  ]

  block_device_mappings {
    device_name = "/dev/sda1"
    ebs {
      volume_size = 20
      volume_type = "gp3"
    }
  }

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
    REDIS_URL=redis://${aws_instance.redis.private_ip}:6379/0
    RABBITMQ_URL=amqp://bite:bite_pass@${aws_instance.rabbitmq.private_ip}:5672/bite_vhost
    AUTH_SERVICE_URL=http://${aws_instance.manejador_autenticacion.private_ip}:8004
    AUTH_SERVICE_TIMEOUT=10
    RATE_LIMIT_ENABLED=true
    RATE_LIMIT_REQUESTS=10
    RATE_LIMIT_WINDOW=60
    SEGURIDAD_URL=http://${aws_instance.manejador_seguridad.private_ip}:8005
    ALLOWED_HOSTS=*
    DEBUG=True
    SECRET_KEY=bite-terraform-secret-key
    EMAIL_BACKEND=django.core.mail.backends.console.EmailBackend
    ENV

    export DATABASE_HOST=${aws_db_instance.main.address}
    export DATABASE_PORT=5432
    export DATABASE_NAME=usuarios_db
    export DATABASE_USER=usuarios_user
    export DATABASE_PASSWORD='Usuarios_2024!'
    export REDIS_URL=redis://${aws_instance.redis.private_ip}:6379/0
    export RABBITMQ_URL=amqp://bite:bite_pass@${aws_instance.rabbitmq.private_ip}:5672/bite_vhost
    export AUTH_SERVICE_URL=http://${aws_instance.manejador_autenticacion.private_ip}:8004
    export AUTH_SERVICE_TIMEOUT=10
    export RATE_LIMIT_ENABLED=true
    export RATE_LIMIT_REQUESTS=10
    export RATE_LIMIT_WINDOW=60
    export SEGURIDAD_URL=http://${aws_instance.manejador_seguridad.private_ip}:8005
    export ALLOWED_HOSTS=*
    export DEBUG=True
    export SECRET_KEY=bite-terraform-secret-key

    sudo apt-get update -y
    sudo apt-get install -y postgresql-client --fix-missing

    ${local.git_bootstrap}

    until nc -z ${aws_db_instance.main.address} 5432; do sleep 5; done
    until nc -z ${aws_instance.redis.private_ip} 6379; do sleep 5; done
    until nc -z ${aws_instance.rabbitmq.private_ip} 5672; do sleep 5; done
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
    sudo python3 -m pip install -r requirements.txt -q
    python3 manage.py migrate --noinput || true
    python3 manage.py seed_usuarios_data || echo "seed failed, continuing"
    nohup python3 manage.py runserver 0.0.0.0:8001 > /var/log/manejador_usuarios.log 2>&1 &
  EOT
  )

  tag_specifications {
    resource_type = "instance"
    tags = merge(local.common_tags, {
      Name    = "${var.project_prefix}-manejador-usuarios"
      Role    = "app-server"
      Service = "usuarios"
    })
  }
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

  depends_on = [
    aws_db_instance.main,
    aws_instance.redis,
    aws_instance.rabbitmq,
    aws_autoscaling_group.cloud,
    aws_instance.manejador_autenticacion,
    aws_lb_target_group.usuarios,
  ]

  tag {
    key                 = "Project"
    value               = local.project_name
    propagate_at_launch = true
  }
}

# -----------------------------------------------------------------------------
# manejador_cloud — FastAPI + Auto Scaling Group (ASR16 latency support)
# Replaces the fixed EC2 instance with a launch template + ASG so the cloud
# service can scale horizontally behind the ALB for latency experiments.
# CQRS: launch template passes both DATABASE_HOST (write) and
#       DATABASE_READ_HOST (read replica) as env vars.
# -----------------------------------------------------------------------------

resource "aws_launch_template" "cloud" {
  name_prefix   = "${var.project_prefix}-cloud-"
  image_id      = data.aws_ami.ubuntu.id
  instance_type = var.instance_type_app
  key_name      = var.key_name

  vpc_security_group_ids = [
    aws_security_group.app.id,
    aws_security_group.ssh.id,
  ]

  block_device_mappings {
    device_name = "/dev/sda1"
    ebs {
      volume_size = 20
      volume_type = "gp3"
    }
  }

  user_data = base64encode(<<-EOT
    #!/bin/bash
    set -euxo pipefail
    export DEBIAN_FRONTEND=noninteractive

    sudo bash -c 'cat > /etc/environment' <<ENVEOF
DATABASE_HOST=${aws_db_instance.main.address}
DATABASE_READ_HOST=${aws_db_instance.cloud_read_replica.address}
DATABASE_PORT=5432
DATABASE_NAME=cloud_db
DATABASE_USER=cloud_user
DATABASE_PASSWORD=Cloud_2024!
REDIS_URL=redis://${aws_instance.redis.private_ip}:6379/1
AUTH_SERVICE_URL=http://${aws_instance.manejador_autenticacion.private_ip}:8004
AUTH_DISABLED=true
AUTH_DISABLED_TENANT=550e8400-e29b-41d4-a716-446655440001
RATE_LIMIT_ENABLED=true
RATE_LIMIT_REQUESTS=20
RATE_LIMIT_WINDOW=10
SEGURIDAD_URL=http://${aws_instance.manejador_seguridad.private_ip}:8005
CORS_ALLOWED_ORIGINS=http://${aws_lb.main.dns_name}
ALLOWED_HOSTS=*
DEBUG=True
SECRET_KEY=bite-terraform-secret-key
ENVEOF

    export DATABASE_HOST=${aws_db_instance.main.address}
    export DATABASE_READ_HOST=${aws_db_instance.cloud_read_replica.address}
    export DATABASE_PORT=5432
    export DATABASE_NAME=cloud_db
    export DATABASE_USER=cloud_user
    export DATABASE_PASSWORD='Cloud_2024!'
    export REDIS_URL=redis://${aws_instance.redis.private_ip}:6379/1
    export AUTH_SERVICE_URL=http://${aws_instance.manejador_autenticacion.private_ip}:8004
    export AUTH_DISABLED=true
    export AUTH_DISABLED_TENANT=550e8400-e29b-41d4-a716-446655440001
    export RATE_LIMIT_ENABLED=true
    export RATE_LIMIT_REQUESTS=20
    export RATE_LIMIT_WINDOW=10
    export SEGURIDAD_URL=http://${aws_instance.manejador_seguridad.private_ip}:8005
    export CORS_ALLOWED_ORIGINS=http://${aws_lb.main.dns_name}
    export ALLOWED_HOSTS=*
    export DEBUG=True
    export SECRET_KEY=bite-terraform-secret-key

    sudo apt-get update -y
    sudo apt-get install -y postgresql-client --fix-missing

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
    sudo python3 -m pip install -r requirements.txt -q
    python3 setup_db.py || echo "setup_db failed, continuing"
    python3 seed_cloud_data.py || echo "seed failed, continuing"
    sudo touch /var/log/manejador_cloud.log && sudo chmod 666 /var/log/manejador_cloud.log
    nohup uvicorn main:app --host 0.0.0.0 --port 8002 --workers 4 \
      > /var/log/manejador_cloud.log 2>&1 &
  EOT
  )

  tag_specifications {
    resource_type = "instance"
    tags = merge(local.common_tags, {
      Name    = "${var.project_prefix}-manejador-cloud"
      Role    = "app-server"
      Service = "cloud"
    })
  }
}

resource "aws_autoscaling_group" "cloud" {
  name                = "${var.project_prefix}-asg-cloud"
  min_size            = 1
  max_size            = 4
  desired_capacity    = 1
  vpc_zone_identifier = tolist(data.aws_subnets.default.ids)
  target_group_arns   = [aws_lb_target_group.cloud.arn]

  launch_template {
    id      = aws_launch_template.cloud.id
    version = "$Latest"
  }

  depends_on = [
    aws_db_instance.main,
    aws_db_instance.cloud_read_replica,
    aws_instance.redis,
    aws_lb_target_group.cloud,
  ]

  tag {
    key                 = "Project"
    value               = local.project_name
    propagate_at_launch = true
  }
}

resource "aws_autoscaling_policy" "cloud_scale_out" {
  name                   = "${var.project_prefix}-cloud-scale-out"
  autoscaling_group_name = aws_autoscaling_group.cloud.name
  adjustment_type        = "ChangeInCapacity"
  scaling_adjustment     = 1
  cooldown               = 120
}

resource "aws_autoscaling_policy" "cloud_scale_in" {
  name                   = "${var.project_prefix}-cloud-scale-in"
  autoscaling_group_name = aws_autoscaling_group.cloud.name
  adjustment_type        = "ChangeInCapacity"
  scaling_adjustment     = -1
  cooldown               = 300
}

resource "aws_cloudwatch_metric_alarm" "cloud_cpu_high" {
  alarm_name          = "${var.project_prefix}-cloud-cpu-high"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 2
  metric_name         = "CPUUtilization"
  namespace           = "AWS/EC2"
  period              = 60
  statistic           = "Average"
  threshold           = 70
  alarm_actions       = [aws_autoscaling_policy.cloud_scale_out.arn]

  dimensions = {
    AutoScalingGroupName = aws_autoscaling_group.cloud.name
  }
}

resource "aws_cloudwatch_metric_alarm" "cloud_cpu_low" {
  alarm_name          = "${var.project_prefix}-cloud-cpu-low"
  comparison_operator = "LessThanThreshold"
  evaluation_periods  = 5
  metric_name         = "CPUUtilization"
  namespace           = "AWS/EC2"
  period              = 60
  statistic           = "Average"
  threshold           = 30
  alarm_actions       = [aws_autoscaling_policy.cloud_scale_in.arn]

  dimensions = {
    AutoScalingGroupName = aws_autoscaling_group.cloud.name
  }
}

# -----------------------------------------------------------------------------
# manejador_reportes — Django + Auto Scaling Group (ASR17 scalability support)
# ASG allows horizontal scaling of the event processor under high batch load.
# -----------------------------------------------------------------------------

resource "aws_launch_template" "reportes" {
  name_prefix   = "${var.project_prefix}-reportes-"
  image_id      = data.aws_ami.ubuntu.id
  instance_type = var.instance_type_app
  key_name      = var.key_name

  vpc_security_group_ids = [
    aws_security_group.app.id,
    aws_security_group.ssh.id,
  ]

  block_device_mappings {
    device_name = "/dev/sda1"
    ebs {
      volume_size = 20
      volume_type = "gp3"
    }
  }

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
    REDIS_URL=redis://${aws_instance.redis.private_ip}:6379/2
    CELERY_RESULT_BACKEND=redis://${aws_instance.redis.private_ip}:6379/3
    RABBITMQ_URL=amqp://bite:bite_pass@${aws_instance.rabbitmq.private_ip}:5672/bite_vhost
    CELERY_BROKER_URL=amqp://bite:bite_pass@${aws_instance.rabbitmq.private_ip}:5672/bite_vhost
    ALLOWED_HOSTS=*
    DEBUG=True
    SECRET_KEY=bite-terraform-secret-key
    EMAIL_BACKEND=django.core.mail.backends.console.EmailBackend
    ENV

    export DATABASE_HOST=${aws_db_instance.main.address}
    export DATABASE_PORT=5432
    export DATABASE_NAME=reportes_db
    export DATABASE_USER=reportes_user
    export DATABASE_PASSWORD='Reportes_2024!'
    export REDIS_URL=redis://${aws_instance.redis.private_ip}:6379/2
    export CELERY_RESULT_BACKEND=redis://${aws_instance.redis.private_ip}:6379/3
    export RABBITMQ_URL=amqp://bite:bite_pass@${aws_instance.rabbitmq.private_ip}:5672/bite_vhost
    export CELERY_BROKER_URL=amqp://bite:bite_pass@${aws_instance.rabbitmq.private_ip}:5672/bite_vhost
    export ALLOWED_HOSTS=*
    export DEBUG=True
    export SECRET_KEY=bite-terraform-secret-key

    sudo apt-get update -y
    sudo apt-get install -y postgresql-client --fix-missing

    ${local.git_bootstrap}

    until nc -z ${aws_db_instance.main.address} 5432; do sleep 5; done
    until nc -z ${aws_instance.redis.private_ip} 6379; do sleep 5; done
    until nc -z ${aws_instance.rabbitmq.private_ip} 5672; do sleep 5; done

    PGPASSWORD='Bite_Master_2024!' psql -h ${aws_db_instance.main.address} -U bite_master -d bite_master \
      -c "CREATE DATABASE reportes_db;" || true
    PGPASSWORD='Bite_Master_2024!' psql -h ${aws_db_instance.main.address} -U bite_master -d bite_master \
      -c "CREATE USER reportes_user WITH PASSWORD 'Reportes_2024!';" || true
    PGPASSWORD='Bite_Master_2024!' psql -h ${aws_db_instance.main.address} -U bite_master -d bite_master \
      -c "GRANT ALL PRIVILEGES ON DATABASE reportes_db TO reportes_user;" || true
    PGPASSWORD='Bite_Master_2024!' psql -h ${aws_db_instance.main.address} -U bite_master -d reportes_db \
      -c "GRANT ALL ON SCHEMA public TO reportes_user;" || true

    cd ${local.repo_dir}/manejador_reportes
    sudo python3 -m pip install -r requirements.txt -q
    python3 manage.py migrate --noinput || true
    python3 manage.py seed_reportes_data || echo "seed failed, continuing"
    nohup python3 manage.py runserver 0.0.0.0:8003 > /var/log/manejador_reportes.log 2>&1 &
  EOT
  )

  tag_specifications {
    resource_type = "instance"
    tags = merge(local.common_tags, {
      Name    = "${var.project_prefix}-manejador-reportes"
      Role    = "app-server"
      Service = "reportes"
    })
  }
}

resource "aws_autoscaling_group" "reportes" {
  name                = "${var.project_prefix}-asg-reportes"
  min_size            = 1
  max_size            = 4
  desired_capacity    = 1
  vpc_zone_identifier = tolist(data.aws_subnets.default.ids)
  target_group_arns   = [aws_lb_target_group.reportes.arn]

  launch_template {
    id      = aws_launch_template.reportes.id
    version = "$Latest"
  }

  depends_on = [
    aws_db_instance.main,
    aws_instance.redis,
    aws_instance.rabbitmq,
    aws_lb_target_group.reportes,
  ]

  tag {
    key                 = "Project"
    value               = local.project_name
    propagate_at_launch = true
  }
}

# -----------------------------------------------------------------------------
# CELERY WORKER POOL
# architecture.md §4.1 - Worker Pool (Auto-scaling) for ASR17
# Two EC2 instances running Celery workers, each with configurable concurrency
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
    aws_instance.redis,
    aws_instance.rabbitmq,
    aws_autoscaling_group.reportes,
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
    REDIS_URL=redis://${aws_instance.redis.private_ip}:6379/2
    CELERY_RESULT_BACKEND=redis://${aws_instance.redis.private_ip}:6379/3
    RABBITMQ_URL=amqp://bite:bite_pass@${aws_instance.rabbitmq.private_ip}:5672/bite_vhost
    CELERY_BROKER_URL=amqp://bite:bite_pass@${aws_instance.rabbitmq.private_ip}:5672/bite_vhost
    CELERY_WORKER_CONCURRENCY=${var.celery_worker_concurrency}
    DEBUG=True
    SECRET_KEY=bite-terraform-secret-key
    ENV

    export DATABASE_HOST=${aws_db_instance.main.address}
    export DATABASE_PORT=5432
    export DATABASE_NAME=reportes_db
    export DATABASE_USER=reportes_user
    export DATABASE_PASSWORD='Reportes_2024!'
    export REDIS_URL=redis://${aws_instance.redis.private_ip}:6379/2
    export CELERY_RESULT_BACKEND=redis://${aws_instance.redis.private_ip}:6379/3
    export RABBITMQ_URL=amqp://bite:bite_pass@${aws_instance.rabbitmq.private_ip}:5672/bite_vhost
    export CELERY_BROKER_URL=amqp://bite:bite_pass@${aws_instance.rabbitmq.private_ip}:5672/bite_vhost
    export CELERY_WORKER_CONCURRENCY=${var.celery_worker_concurrency}
    export DEBUG=True
    export SECRET_KEY=bite-terraform-secret-key

    ${local.git_bootstrap}

    until nc -z ${aws_db_instance.main.address} 5432; do sleep 5; done
    until nc -z ${aws_instance.redis.private_ip} 6379; do sleep 5; done
    until nc -z ${aws_instance.rabbitmq.private_ip} 5672; do sleep 5; done

    cd ${local.repo_dir}/manejador_reportes
    sudo python3 -m pip install -r requirements.txt
    # Celery reads directly from RabbitMQ - no pika consumer middleman
    nohup python3 -m celery -A manejador_reportes.celery worker \
      --loglevel=info \
      --concurrency=${var.celery_worker_concurrency} \
      > /var/log/bite-worker-${each.key}.log 2>&1 &
  EOT

  tags = merge(local.common_tags, {
    Name = "${var.project_prefix}-worker-${each.key}"
    Role = "worker"
  })
}

# -----------------------------------------------------------------------------
# APPLICATION LOAD BALANCER
# architecture.md §3.2 - AWS Application Load Balancer
# Routes ASR16 traffic → manejador_cloud ASG (port 8002) via /projects/*  (local DB, no HTTP hop)
# Routes ASR17 traffic → manejador_reportes  (port 8003) via /events/* /reports/*
# Routes cloud CQRS   → manejador_cloud ASG  (port 8002) via /cloud-accounts/*
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

# Target group for manejador_cloud ASG — CQRS FastAPI (ASR16 support)
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

# manejador_usuarios and manejador_reportes register to their target groups
# automatically via target_group_arns in their ASG resources.
# manejador_autenticacion and manejador_seguridad are fixed EC2s — explicit attachment needed.

# ALB Listener - HTTP on port 80
# ASR2: redirects HTTP -> HTTPS to enforce 100% encrypted traffic
resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.main.arn
  port              = 80
  protocol          = "HTTP"

  # ASR2 – Redirect HTTP to HTTPS (proves HTTP is rejected / redirected)
  default_action {
    type = "redirect"
    redirect {
      port        = "443"
      protocol    = "HTTPS"
      status_code = "HTTP_301"
    }
  }
}

# ASR2 – Self-signed TLS certificate for AWS Academy (no real domain needed)
# Generates a private key + self-signed cert directly on the ALB via ACM import
resource "aws_acm_certificate" "asr2_selfsigned" {
  private_key      = tls_private_key.asr2.private_key_pem
  certificate_body = tls_self_signed_cert.asr2.cert_pem

  tags = merge(local.common_tags, {
    Name = "${var.project_prefix}-asr2-selfsigned"
    ASR  = "ASR2-Integridad"
  })
}

resource "tls_private_key" "asr2" {
  algorithm = "RSA"
  rsa_bits  = 2048
}

resource "tls_self_signed_cert" "asr2" {
  private_key_pem = tls_private_key.asr2.private_key_pem

  subject {
    common_name  = "bite2-alb.bite.co"
    organization = "BITE.co ASR2 Experiment"
  }

  validity_period_hours = 720 # 30 days

  allowed_uses = [
    "key_encipherment",
    "digital_signature",
    "server_auth",
  ]
}

# ASR2 – HTTPS listener on port 443 (TLS termination at the ALB)
resource "aws_lb_listener" "https" {
  load_balancer_arn = aws_lb.main.arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = aws_acm_certificate.asr2_selfsigned.arn

  # Default action routes to seguridad (ASR2 tls-status endpoint)
  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.seguridad.arn
  }
}

resource "aws_lb_listener_rule" "events" {
  listener_arn = aws_lb_listener.http.arn
  priority     = 10

  action {
    type = "redirect"
    redirect {
      port        = "443"
      protocol    = "HTTPS"
      status_code = "HTTP_301"
    }
  }

  condition {
    path_pattern {
      values = ["/events/*", "/events"]
    }
  }
}

resource "aws_lb_listener_rule" "reports" {
  listener_arn = aws_lb_listener.http.arn
  priority     = 20

  action {
    type = "redirect"
    redirect {
      port        = "443"
      protocol    = "HTTPS"
      status_code = "HTTP_301"
    }
  }

  condition {
    path_pattern {
      values = ["/reports", "/reports/*"]
    }
  }
}

# HTTP → HTTPS redirect for /cloud-accounts/* (ASR2 TLS enforcement)
resource "aws_lb_listener_rule" "cloud" {
  listener_arn = aws_lb_listener.http.arn
  priority     = 25

  action {
    type = "redirect"
    redirect {
      port        = "443"
      protocol    = "HTTPS"
      status_code = "HTTP_301"
    }
  }

  condition {
    path_pattern {
      values = ["/cloud-accounts", "/cloud-accounts/*"]
    }
  }
}

# HTTP → HTTPS redirect for /projects/* (ASR2 TLS enforcement)
resource "aws_lb_listener_rule" "projects_redirect" {
  listener_arn = aws_lb_listener.http.arn
  priority     = 30

  action {
    type = "redirect"
    redirect {
      port        = "443"
      protocol    = "HTTPS"
      status_code = "HTTP_301"
    }
  }

  condition {
    path_pattern {
      values = ["/projects", "/projects/*"]
    }
  }
}

# -----------------------------------------------------------------------------
# COGNITO USER POOL (ASR3 – Tenant Identity)
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
}

# Test users are created via management command (seed_auth_users) on first startup.
# In Cognito (production), create them manually or via AWS CLI after apply:
#
#   aws cognito-idp admin-create-user \
#     --user-pool-id <user_pool_id> \
#     --username empresa_a@bite.co \
#     --temporary-password BiteCo2024! \
#     --user-attributes Name=email,Value=empresa_a@bite.co Name=custom:empresa_id,Value=550e8400-e29b-41d4-a716-446655440001 Name=custom:rol,Value=ADMIN
#
#   aws cognito-idp admin-create-user \
#     --user-pool-id <user_pool_id> \
#     --username empresa_b@bite.co \
#     --temporary-password BiteCo2024! \
#     --user-attributes Name=email,Value=empresa_b@bite.co Name=custom:empresa_id,Value=550e8400-e29b-41d4-a716-446655440002 Name=custom:rol,Value=MANAGER

# -----------------------------------------------------------------------------
# SECURITY GROUP FOR AUTH SERVICES
# Ports 8004 (autenticacion) and 8005 (seguridad) — ingress from ALB only
# -----------------------------------------------------------------------------

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

# -----------------------------------------------------------------------------
# MANEJADOR_AUTENTICACION — port 8004
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
    COGNITO_USER_POOL_ID=${aws_cognito_user_pool.bite.id}
    COGNITO_CLIENT_ID=${aws_cognito_user_pool_client.bite_spa.id}
    COGNITO_REGION=${var.region}
    LOCAL_JWT_SECRET=bite-local-jwt-secret
    ALLOWED_HOSTS=*
    DEBUG=False
    SECRET_KEY=bite-terraform-secret-key
    ENV

    export DATABASE_HOST=${aws_db_instance.main.address}
    export DATABASE_PORT=5432
    export DATABASE_NAME=seguridad_db
    export DATABASE_USER=seguridad_user
    export DATABASE_PASSWORD='Seguridad_2024!'
    export COGNITO_USER_POOL_ID=${aws_cognito_user_pool.bite.id}
    export COGNITO_CLIENT_ID=${aws_cognito_user_pool_client.bite_spa.id}
    export COGNITO_REGION=${var.region}
    export LOCAL_JWT_SECRET=bite-local-jwt-secret
    export ALLOWED_HOSTS=*
    export DEBUG=False
    export SECRET_KEY=bite-terraform-secret-key

    ${local.git_bootstrap}

    until nc -z ${aws_db_instance.main.address} 5432; do sleep 5; done

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

    sudo tee /etc/systemd/system/manejador-autenticacion.service <<SERVICE
[Unit]
Description=BITE.co manejador_autenticacion
After=network.target

[Service]
User=ubuntu
WorkingDirectory=${local.repo_dir}/manejador_autenticacion
EnvironmentFile=/etc/environment
ExecStart=/usr/bin/python3 manage.py runserver 0.0.0.0:8004
Restart=always
RestartSec=5
StandardOutput=append:/var/log/manejador_autenticacion.log
StandardError=append:/var/log/manejador_autenticacion.log

[Install]
WantedBy=multi-user.target
SERVICE

    sudo systemctl daemon-reload
    sudo systemctl enable manejador-autenticacion
    sudo systemctl start manejador-autenticacion
  EOT

  tags = merge(local.common_tags, {
    Name    = "${var.project_prefix}-manejador-autenticacion"
    Role    = "app-server"
    Service = "autenticacion"
  })
}

# -----------------------------------------------------------------------------
# MANEJADOR_SEGURIDAD — port 8005
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
    MONGO_URI=mongodb://admin_mongo:Mongo_2024!@127.0.0.1:27017/seguridad_logs?authSource=admin
    AUTH_SERVICE_URL=http://${aws_instance.manejador_autenticacion.private_ip}:8004
    AUTH_SERVICE_TIMEOUT=2
    LOCAL_JWT_SECRET=bite-local-jwt-secret
    COGNITO_USER_POOL_ID=${aws_cognito_user_pool.bite.id}
    COGNITO_CLIENT_ID=${aws_cognito_user_pool_client.bite_spa.id}
    COGNITO_REGION=${var.region}
    ALLOWED_HOSTS=*
    DEBUG=False
    SECRET_KEY=bite-terraform-secret-key
    ENV

    export DATABASE_HOST=${aws_db_instance.main.address}
    export DATABASE_PORT=5432
    export DATABASE_NAME=seguridad_db
    export DATABASE_USER=seguridad_user
    export DATABASE_PASSWORD='Seguridad_2024!'
    export MONGO_URI=mongodb://admin_mongo:Mongo_2024!@127.0.0.1:27017/seguridad_logs?authSource=admin
    export AUTH_SERVICE_URL=http://${aws_instance.manejador_autenticacion.private_ip}:8004
    export AUTH_SERVICE_TIMEOUT=2
    export LOCAL_JWT_SECRET=bite-local-jwt-secret
    export COGNITO_USER_POOL_ID=${aws_cognito_user_pool.bite.id}
    export COGNITO_CLIENT_ID=${aws_cognito_user_pool_client.bite_spa.id}
    export COGNITO_REGION=${var.region}
    export ALLOWED_HOSTS=*
    export DEBUG=False
    export SECRET_KEY=bite-terraform-secret-key

    ${local.git_bootstrap}

    until nc -z ${aws_db_instance.main.address} 5432; do sleep 5; done
    until nc -z ${aws_instance.manejador_autenticacion.private_ip} 8004; do sleep 5; done

    # ── Install MongoDB 7.0 ──────────────────────────────────────────────────
    curl -fsSL https://www.mongodb.org/static/pgp/server-7.0.asc \
      | sudo gpg --dearmor -o /usr/share/keyrings/mongodb-server-7.0.gpg
    echo "deb [ arch=amd64,arm64 signed-by=/usr/share/keyrings/mongodb-server-7.0.gpg ] https://repo.mongodb.org/apt/ubuntu jammy/mongodb-org/7.0 multiverse" \
      | sudo tee /etc/apt/sources.list.d/mongodb-org-7.0.list
    sudo apt-get update -y
    sudo apt-get install -y mongodb-org
    sudo systemctl enable mongod
    sudo systemctl start mongod
    # Wait until mongod is accepting connections
    until mongosh --eval "db.adminCommand('ping')" --quiet 2>/dev/null; do sleep 3; done
    # Create admin user (idempotent — fails silently if already exists)
    mongosh admin --eval "
      try {
        db.createUser({ user: 'admin_mongo', pwd: 'Mongo_2024!', roles: [{ role: 'root', db: 'admin' }] });
      } catch(e) { print('user may already exist: ' + e); }
    " || true

    # ── Install app & create systemd service ────────────────────────────────
    cd ${local.repo_dir}/manejador_seguridad
    sudo python3 -m pip install -r requirements.txt
    python3 manage.py migrate --noinput || true

    sudo tee /etc/systemd/system/manejador-seguridad.service <<SERVICE
[Unit]
Description=BITE.co manejador_seguridad
After=network.target mongod.service
Requires=mongod.service

[Service]
User=ubuntu
WorkingDirectory=${local.repo_dir}/manejador_seguridad
EnvironmentFile=/etc/environment
ExecStart=/usr/bin/python3 manage.py runserver 0.0.0.0:8005
Restart=always
RestartSec=5
StandardOutput=append:/var/log/manejador_seguridad.log
StandardError=append:/var/log/manejador_seguridad.log

[Install]
WantedBy=multi-user.target
SERVICE

    sudo systemctl daemon-reload
    sudo systemctl enable manejador-seguridad
    sudo systemctl start manejador-seguridad
  EOT

  tags = merge(local.common_tags, {
    Name    = "${var.project_prefix}-manejador-seguridad"
    Role    = "app-server"
    Service = "seguridad"
  })
}

# -----------------------------------------------------------------------------
# S3 FRONTEND BUCKET — static HTML/CSS/JS site (deploy: aws s3 sync frontend/ s3://<bucket>/)
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

# Auto-upload frontend files — config.js is generated from template with live ALB DNS.
# Terraform re-uploads files whenever their content changes (etag tracking).

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

# -----------------------------------------------------------------------------
# ALB UPDATES — add listener rules for /auth/* and /security/*
# -----------------------------------------------------------------------------

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

resource "aws_lb_listener_rule" "auth" {
  listener_arn = aws_lb_listener.http.arn
  priority     = 5

  action {
    type = "redirect"
    redirect {
      port        = "443"
      protocol    = "HTTPS"
      status_code = "HTTP_301"
    }
  }

  condition {
    path_pattern {
      values = ["/auth/*", "/auth"]
    }
  }
}

resource "aws_lb_listener_rule" "security" {
  listener_arn = aws_lb_listener.http.arn
  priority     = 6

  action {
    type = "redirect"
    redirect {
      port        = "443"
      protocol    = "HTTPS"
      status_code = "HTTP_301"
    }
  }

  condition {
    path_pattern {
      values = ["/security/*", "/security"]
    }
  }
}

# ASR2 – HTTPS listener rules (mirror of HTTP rules, now over TLS)
resource "aws_lb_listener_rule" "https_auth" {
  listener_arn = aws_lb_listener.https.arn
  priority     = 5

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.autenticacion.arn
  }

  condition {
    path_pattern {
      values = ["/auth/*", "/auth"]
    }
  }
}

resource "aws_lb_listener_rule" "https_security" {
  listener_arn = aws_lb_listener.https.arn
  priority     = 6

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.seguridad.arn
  }

  condition {
    path_pattern {
      values = ["/security/*", "/security"]
    }
  }
}

# /projects/* → manejador_cloud ASG (ASR16: local DB validation, no inter-service hop)
resource "aws_lb_listener_rule" "https_projects" {
  listener_arn = aws_lb_listener.https.arn
  priority     = 10

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.cloud.arn
  }

  condition {
    path_pattern {
      values = ["/projects/*", "/projects"]
    }
  }
}

resource "aws_lb_listener_rule" "https_events" {
  listener_arn = aws_lb_listener.https.arn
  priority     = 15

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.reportes.arn
  }

  condition {
    path_pattern {
      values = ["/events/*", "/events"]
    }
  }
}

resource "aws_lb_listener_rule" "https_reports" {
  listener_arn = aws_lb_listener.https.arn
  priority     = 20

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.reportes.arn
  }

  condition {
    path_pattern {
      values = ["/reports", "/reports/*"]
    }
  }
}

# HTTPS forward for /cloud-accounts/* → manejador_cloud ASG
resource "aws_lb_listener_rule" "https_cloud" {
  listener_arn = aws_lb_listener.https.arn
  priority     = 25

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.cloud.arn
  }

  condition {
    path_pattern {
      values = ["/cloud-accounts", "/cloud-accounts/*"]
    }
  }
}

# -----------------------------------------------------------------------------
# OUTPUTS - use these in JMeter HTTP Request samplers
# -----------------------------------------------------------------------------

output "alb_dns_name" {
  description = "ALB DNS name - use this as the JMeter host for both experiments"
  value       = aws_lb.main.dns_name
}

output "alb_usuarios_url" {
  description = "ASR16 latency experiment endpoint (HTTPS)"
  value       = "https://${aws_lb.main.dns_name}/projects"
}

output "alb_reportes_url" {
  description = "ASR17 scalability experiment endpoint (HTTPS)"
  value       = "https://${aws_lb.main.dns_name}/events/batch"
}

output "cloud_asg_name" {
  description = "manejador_cloud Auto Scaling Group name"
  value       = aws_autoscaling_group.cloud.name
}

output "cloud_read_replica_endpoint" {
  description = "RDS read replica endpoint — DATABASE_READ_HOST for manejador_cloud CQRS"
  value       = aws_db_instance.cloud_read_replica.address
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
  description = "RDS PostgreSQL endpoint — all microservices connect here"
  value       = aws_db_instance.main.address
}

output "worker_public_ips" {
  description = "Celery worker pool public IPs - for SSH debugging"
  value       = { for id, instance in aws_instance.worker_pool : id => instance.public_ip }
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
  description = "ASR2/ASR3 auth endpoint via ALB"
  value       = "http://${aws_lb.main.dns_name}/auth/login"
}

# ASR2 – Integridad: HTTPS endpoints for the experiment
output "asr2_tls_status_url_http" {
  description = "ASR2 experiment: HTTP request (should be rejected/redirected)"
  value       = "http://${aws_lb.main.dns_name}/security/tls-status"
}

output "asr2_tls_status_url_https" {
  description = "ASR2 experiment: HTTPS request (should be accepted with TLS info)"
  value       = "https://${aws_lb.main.dns_name}/security/tls-status"
}

output "asr2_integrity_check_url" {
  description = "ASR2 experiment: HMAC integrity check endpoint"
  value       = "https://${aws_lb.main.dns_name}/security/integrity-check"
}

output "asr2_integrity_log_url" {
  description = "ASR2 experiment: audit log for all TLS/integrity checks"
  value       = "https://${aws_lb.main.dns_name}/security/integrity-log"
}

resource "aws_wafv2_web_acl" "rate_limit" {
  name  = "bite2-rate-limit"
  scope = "REGIONAL"

  default_action {
    allow {}
  }

  rule {
    name     = "PostProjectsRateLimit"
    priority = 1

    action {
      block {}
    }

    statement {
      rate_based_statement {
        limit              = 300
        aggregate_key_type = "IP"

        scope_down_statement {
          byte_match_statement {
            field_to_match {
              uri_path {}
            }
            positional_constraint = "STARTS_WITH"
            search_string         = "/projects"
            text_transformation {
              priority = 0
              type     = "NONE"
            }
          }
        }
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "PostProjectsRateLimit"
      sampled_requests_enabled   = true
    }
  }

  visibility_config {
    cloudwatch_metrics_enabled = true
    metric_name                = "bite2-waf"
    sampled_requests_enabled   = true
  }
}

resource "aws_wafv2_web_acl_association" "main" {
  resource_arn = aws_lb.main.arn
  web_acl_arn  = aws_wafv2_web_acl.rate_limit.arn
}

# Contributing to NetBox Arista Lab Workshop

Thank you for your interest in contributing! This document outlines the process for contributing to this project.

## Branch Protection

The `main` branch is protected and requires:

1. **All CI checks to pass** before merging
2. **Pull request review** (at least 1 approval)
3. **No direct pushes** - all changes must go through a PR

## Development Workflow

### 1. Fork and Clone

```bash
git clone https://github.com/AheadAviation/workshop_arista_automation.git
cd workshop_arista_automation
```

### 2. Create a Feature Branch

```bash
git checkout -b feature/your-feature-name
```

### 3. Install Pre-commit Hooks (Recommended)

```bash
pip install pre-commit
pre-commit install
```

This will automatically run linters before each commit.

### 4. Make Your Changes

- Follow existing code style
- Add/update tests if applicable
- Update documentation if needed

### 5. Run Linters Locally

```bash
# YAML lint
pip install yamllint
yamllint -c .yamllint.yml .

# Ansible lint
pip install ansible-lint
ansible-lint playbooks/ roles/

# Python lint
pip install ruff black
ruff check scripts/ filter_plugins/
black --check scripts/ filter_plugins/
```

### 6. Commit and Push

```bash
git add .
git commit -m "feat: description of your changes"
git push origin feature/your-feature-name
```

### 7. Create Pull Request

- Open a PR against `main`
- Fill out the PR template
- Wait for CI checks to pass
- Request review

## CI Checks

The following checks run automatically on every PR:

| Check | Description |
|-------|-------------|
| **Ansible Lint** | Validates Ansible playbooks and roles |
| **YAML Lint** | Checks YAML syntax and formatting |
| **Python Lint (Ruff)** | Lints Python scripts |
| **Python Format (Black)** | Checks Python formatting |
| **Jinja2 Lint** | Validates Jinja2 template syntax |
| **Dockerfile Lint** | Validates Dockerfiles with Hadolint |
| **Security Scan** | Scans for vulnerabilities with Trivy |
| **Syntax Check** | Validates Ansible playbook syntax |

All checks must pass before merging to `main`.

## Code Style

### Python
- Use [Black](https://black.readthedocs.io/) for formatting
- Use [Ruff](https://docs.astral.sh/ruff/) for linting
- Max line length: 100 characters

### YAML/Ansible
- 2-space indentation
- Use FQCN for non-builtin modules
- Document complex tasks with comments

### Jinja2 Templates
- Use meaningful variable names
- Add comments for complex logic
- Keep templates modular

## Commit Messages

Follow [Conventional Commits](https://www.conventionalcommits.org/):

```
type(scope): description

[optional body]

[optional footer]
```

Types:
- `feat`: New feature
- `fix`: Bug fix
- `docs`: Documentation
- `style`: Formatting
- `refactor`: Code restructuring
- `test`: Adding tests
- `chore`: Maintenance

Examples:
```
feat(templates): add EVPN overlay template
fix(bgp): correct peer group configuration
docs(readme): update workshop steps
```

## Questions?

Open an issue if you have questions or need help!


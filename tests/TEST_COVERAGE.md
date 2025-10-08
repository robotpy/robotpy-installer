# Unit Test Coverage for Deploy Command

## Overview
This document describes the unit test coverage for the `robotpy deploy` command in `robotpy_installer/cli_deploy.py`.

## Test File
`tests/test_deploy.py`

## Test Coverage Summary

### TestWrapSshError (2 tests)
Tests for the `wrap_ssh_error` context manager helper function:
- ✅ `test_wrap_ssh_error_success`: Verifies context manager works when no error occurs
- ✅ `test_wrap_ssh_error_wraps_exception`: Verifies SSH errors are properly wrapped with additional context

### TestDeploy (19 tests)
Tests for the main `Deploy` class functionality:

#### Command Line Arguments
- ✅ `test_parser_arguments`: Verifies all required command-line arguments are registered

#### Package Management
- ✅ `test_init_packages_cache`: Verifies package caches are initialized to None
- ✅ `test_get_cached_packages`: Verifies cached packages are retrieved and cached properly
- ✅ `test_get_robot_packages_caches_result`: Verifies robot packages are cached after first retrieval
- ✅ `test_clear_pip_packages`: Verifies pip packages are uninstalled correctly (except pip itself)

#### File Operations
- ✅ `test_copy_to_tmpdir_basic`: Verifies files are copied to temp directory correctly
- ✅ `test_copy_to_tmpdir_ignores_hidden_files`: Verifies hidden files/directories (`.git`, `.hidden`) are ignored
- ✅ `test_copy_to_tmpdir_ignores_pyc_files`: Verifies compiled Python files (`.pyc`) are ignored
- ✅ `test_copy_to_tmpdir_ignores_pycache`: Verifies `__pycache__` directories are ignored
- ✅ `test_copy_to_tmpdir_ignores_venv`: Verifies virtual environment directories are ignored
- ✅ `test_copy_to_tmpdir_ignores_wheel_files`: Verifies wheel files (`.whl`) are ignored

#### Large File Handling
- ✅ `test_check_large_files_allows_small_files`: Verifies small files pass size check
- ✅ `test_check_large_files_blocks_large_files`: Verifies large files (>250KB) are blocked without confirmation
- ✅ `test_check_large_files_allows_with_confirmation`: Verifies large files are allowed with user confirmation

#### Build Data Generation
- ✅ `test_generate_build_data_basic`: Verifies basic build metadata is generated (host, user, date, path)
- ✅ `test_generate_build_data_with_git`: Verifies git information is included when in a git repo

#### Deploy Workflow
- ✅ `test_run_blocks_home_directory_deploy`: Verifies deploying from home directory is blocked for safety
- ✅ `test_run_tests_by_default`: Verifies tests are run by default before deploy
- ✅ `test_skip_tests_flag`: Verifies `--skip-tests` flag properly skips test execution

### TestDeployIntegration (1 test)
Integration tests for complete deploy workflows:
- ✅ `test_successful_deploy_workflow`: Tests a complete successful deploy from start to finish

## Total Test Count
**22 tests** - All passing ✅

## What's Tested

### Core Functionality
- ✅ Command-line argument parsing
- ✅ File copying and filtering
- ✅ Large file detection and warnings
- ✅ Package caching mechanisms
- ✅ Build metadata generation
- ✅ Git integration
- ✅ Test execution control
- ✅ Safety checks (home directory blocking)

### File Filtering
The tests verify that the following files/directories are properly excluded from deployment:
- Hidden files (starting with `.`)
- `.git` directories
- `__pycache__` directories
- `venv` directories
- `.pyc` files
- `.whl` files
- `.ipk` files
- `.zip` files
- `.gz` files
- `.wpilog` files

### Error Handling
- ✅ SSH error wrapping with context
- ✅ User confirmations for dangerous operations
- ✅ Test failure handling

## What's NOT Tested (Yet)
The following areas would benefit from additional test coverage:

### SSH/Robot Communication
- Robot connection establishment
- File transfer via SFTP
- Remote command execution
- Robot package installation
- Python version checking on robot

### Requirements Management
- `_ensure_requirements()` method
- Package version checking
- Requirement installation/uninstallation flows
- RoboRIO image version validation

### Deploy Execution
- `_do_deploy()` method
- Robot code compilation
- Robot code startup
- Netconsole integration
- Debug mode configuration

### Edge Cases
- Network failures during deploy
- Interrupted deploys
- Concurrent deploys
- Disk space issues on robot
- Permission errors

## Running the Tests

```bash
# Run all deploy tests
python3 -m pytest tests/test_deploy.py -v

# Run specific test class
python3 -m pytest tests/test_deploy.py::TestDeploy -v

# Run specific test
python3 -m pytest tests/test_deploy.py::TestDeploy::test_check_large_files_allows_small_files -v

# Run with coverage report
python3 -m pytest tests/test_deploy.py --cov=robotpy_installer.cli_deploy --cov-report=html
```

## Test Design Patterns

### Mocking Strategy
Tests use `unittest.mock` extensively to:
- Mock SSH connections and avoid requiring actual robot hardware
- Mock file system operations for isolation
- Mock subprocess calls to avoid running actual git/test commands
- Mock package managers to avoid network calls

### Test Isolation
- Each test method is independent
- Temporary directories are created and cleaned up
- No tests modify global state
- Mocks are reset between tests

### Test Structure
Tests follow the Arrange-Act-Assert pattern:
1. **Arrange**: Set up test fixtures and mocks
2. **Act**: Call the method under test
3. **Assert**: Verify expected behavior

## Future Test Expansion
To expand test coverage to other commands in robotpy-installer:
1. Use similar mocking patterns for SSH/network operations
2. Test command-line argument parsing for each command
3. Test error handling and edge cases
4. Add integration tests for complete workflows
5. Consider using pytest fixtures for common setup code

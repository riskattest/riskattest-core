"""Main CLI for MRM Core"""

import sys
import zipfile

import typer
from pathlib import Path
from rich.console import Console
from rich.table import Table
from rich import print as rprint
import logging
from typing import Dict, List, Any, Optional

from mrm.core.project import Project
from mrm.engine.runner import TestRunner
from mrm.tests.library import registry

app = typer.Typer(
    name="mrm",
    help="Model Risk Management CLI - dbt for model validation",
    add_completion=True,
    rich_markup_mode="rich"
)

console = Console()
logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)


@app.command()
def init(
    project_name: str = typer.Argument(..., help="Project name"),
    template: str = typer.Option(None, "--template", "-t", help="Template to use"),
    backend: str = typer.Option("local", "--backend", "-b", help="Default backend")
):
    """Initialize a new MRM project"""
    from mrm.core.init import initialize_project
    
    try:
        project_path = initialize_project(project_name, template, backend)
        console.print(f" Created MRM project: [bold]{project_name}[/bold]", style="green")
        console.print(f"  Location: {project_path}")
        console.print("\nNext steps:")
        console.print(f"  cd {project_name}")
        console.print("  mrm list models")
    except Exception as e:
        console.print(f" Error initializing project: {e}", style="red")
        raise typer.Exit(1)


@app.command()
def test(
    models: str = typer.Option(None, "--models", "-m", help="Models to test"),
    select: str = typer.Option(None, "--select", "-s", help="Selection criteria"),
    exclude: str = typer.Option(None, "--exclude", "-e", help="Models to exclude"),
    suite: str = typer.Option(None, "--suite", help="Test suite to run"),
    fail_fast: bool = typer.Option(False, "--fail-fast", help="Stop on first failure"),
    threads: int = typer.Option(1, "--threads", "-t", help="Parallel threads"),
    profile: str = typer.Option("dev", "--profile", "-p", help="Profile to use")
):
    """Run validation tests"""
    try:
        # Load project
        project = Project.load(profile=profile)
        
        # Select models
        model_configs = project.select_models(
            models=models,
            select=select,
            exclude=exclude
        )
        
        if not model_configs:
            console.print("No models selected", style="yellow")
            raise typer.Exit(0)
        
        console.print(f"Running tests for {len(model_configs)} model(s)...\n")
        
        # Run tests
        runner = TestRunner(project.config, project.backend, project.catalog)
        
        test_selection = None
        if suite:
            test_selection = [suite]
        
        results = runner.run_tests(
            model_configs,
            test_selection=test_selection,
            fail_fast=fail_fast,
            threads=threads
        )
        
        # Display results
        console.print()
        _display_test_results(results)
        
        # Exit with error if any tests failed
        all_passed = all(
            r.get('all_passed', False) 
            for r in results.values() 
            if 'error' not in r
        )
        
        if not all_passed:
            raise typer.Exit(1)
    
    except FileNotFoundError as e:
        console.print(f" {e}", style="red")
        raise typer.Exit(1)
    except Exception as e:
        console.print(f" Error running tests: {e}", style="red")
        logger.exception(e)
        raise typer.Exit(1)


@app.command(name="list")
def list_command(
    resource: str = typer.Argument(..., help="Resource type: models, tests, suites, backends"),
    tier: str = typer.Option(None, "--tier", help="Filter by risk tier"),
    owner: str = typer.Option(None, "--owner", help="Filter by owner"),
    category: str = typer.Option(None, "--category", help="Filter by category"),
    profile: str = typer.Option("dev", "--profile", "-p", help="Profile to use")
):
    """List project resources"""
    try:
        if resource == "models":
            project = Project.load(profile=profile)
            models = project.list_models(tier=tier, owner=owner)
            _display_models_table(models)
        
        elif resource == "tests":
            registry.load_builtin_tests()
            tests = registry.list_tests(category=category)
            _display_tests_table(tests)
        
        elif resource == "suites":
            project = Project.load(profile=profile)
            suites = project.get_test_suites()
            _display_suites_table(suites)
        
        elif resource == "backends":
            project = Project.load(profile=profile)
            backends = project.config.get('backends', {})
            _display_backends_table(backends)
        
        else:
            console.print(f"Unknown resource type: {resource}", style="red")
            console.print("Available types: models, tests, suites, backends")
            raise typer.Exit(1)
    
    except FileNotFoundError as e:
        console.print(f" {e}", style="red")
        raise typer.Exit(1)
    except Exception as e:
        console.print(f" Error listing {resource}: {e}", style="red")
        raise typer.Exit(1)


@app.command()
def debug(
    show_config: bool = typer.Option(False, "--show-config", help="Show project config"),
    show_tests: bool = typer.Option(False, "--show-tests", help="Show available tests"),
    show_dag: bool = typer.Option(False, "--show-dag", help="Show model dependency graph"),
    show_catalog: bool = typer.Option(False, "--show-catalog", help="Show model catalog"),
    profile: str = typer.Option("dev", "--profile", "-p", help="Profile to use")
):
    """Debug project configuration"""
    try:
        project = Project.load(profile=profile)
        
        console.print(f"[bold]Project:[/bold] {project.name}")
        console.print(f"[bold]Version:[/bold] {project.version}")
        console.print(f"[bold]Root:[/bold] {project.root_path}")
        console.print(f"[bold]Profile:[/bold] {profile}")
        console.print(f"[bold]Backend:[/bold] {project.backend.__class__.__name__}")
        
        if show_config:
            console.print("\n[bold]Configuration:[/bold]")
            import json
            console.print(json.dumps(project.config, indent=2))
        
        if show_tests:
            registry.load_builtin_tests()
            console.print(f"\n[bold]Available Tests:[/bold] {len(registry.list_tests())}")
            for test_name in registry.list_tests():
                console.print(f"  - {test_name}")
        
        if show_dag:
            console.print("\n[bold]Model Dependency Graph:[/bold]")
            console.print(project.dag.visualize())
            
            console.print("\n[bold]Execution Levels:[/bold]")
            levels = project.dag.get_execution_levels()
            for i, level in enumerate(levels):
                console.print(f"  Level {i}: {level}")
        
        if show_catalog:
            console.print("\n[bold]Model Catalog:[/bold]")
            for name, ref in project.catalog.models.items():
                console.print(f"  {name}: {ref.source.value} ({ref.identifier})")
    
    except FileNotFoundError as e:
        console.print(f" {e}", style="red")
        raise typer.Exit(1)
    except Exception as e:
        console.print(f" Error: {e}", style="red")
        raise typer.Exit(1)


@app.command()
def publish(
    model: str = typer.Argument(..., help="Model name to publish"),
    to_catalog: str = typer.Option(None, "--to", help="Target catalog (databricks, mlflow)"),
    version: str = typer.Option(None, "--version", help="Version tag"),
    profile: str = typer.Option("dev", "--profile", "-p", help="Profile to use")
):
    """Publish model to external registry (Databricks, MLflow, etc.)"""
    try:
        project = Project.load(profile=profile)
        
        # Find the model
        model_configs = project.select_models(models=model)
        if not model_configs:
            console.print(f"Model not found: {model}", style="red")
            raise typer.Exit(1)
        
        if len(model_configs) > 1:
            console.print(f"Multiple models matched '{model}', be more specific", style="yellow")
            raise typer.Exit(1)
        
        model_config = model_configs[0]
        model_info = model_config['model']
        model_name = model_info.get('name')
        
        console.print(f"Publishing model: [bold]{model_name}[/bold]")
        
        # Get model location
        location = model_info.get('location', {})
        if isinstance(location, str):
            # Parse shorthand like "file/path"
            if location.startswith('file/'):
                model_path = location[5:]
            else:
                model_path = location
        else:
            model_path = location.get('path')
        
        if not model_path:
            console.print("Model location/path not found in config", style="red")
            raise typer.Exit(1)
        
        # Make path absolute relative to project root
        from pathlib import Path
        if not Path(model_path).is_absolute():
            model_path = str(project.root_path / model_path)
        
        if not Path(model_path).exists():
            console.print(f"Model file not found: {model_path}", style="red")
            raise typer.Exit(1)
        
        # Determine target catalog
        catalogs = project.config.get('catalogs', {})
        
        if not catalogs:
            console.print("No catalogs configured. Add a catalog section to mrm_project.yml:", style="yellow")
            console.print("""
catalogs:
  databricks:
    type: databricks_unity
    host: https://your-workspace.cloud.databricks.com
    token: ${DATABRICKS_TOKEN}
    catalog: main
    schema: models
    mlflow_registry: true
""")
            raise typer.Exit(1)
        
        # Find catalog to use
        target_cfg = None
        target_name = to_catalog
        
        if target_name:
            target_cfg = catalogs.get(target_name)
            if not target_cfg:
                console.print(f"Catalog not found: {target_name}", style="red")
                raise typer.Exit(1)
        else:
            # Use first databricks catalog
            for name, cfg in catalogs.items():
                if cfg.get('type') in ('databricks_unity', 'databricks_uc'):
                    target_cfg = cfg
                    target_name = name
                    break
        
        if not target_cfg:
            console.print("No Databricks Unity Catalog found in config", style="red")
            raise typer.Exit(1)
        
        console.print(f"Target catalog: [cyan]{target_name}[/cyan]")
        
        # Publish to Databricks
        from mrm.core.catalog_backends.databricks_unity import DatabricksUnityCatalog
        
        backend = DatabricksUnityCatalog(
            host=target_cfg.get('host'),
            token=target_cfg.get('token'),
            catalog=target_cfg.get('catalog'),
            schema=target_cfg.get('schema'),
            mlflow_registry=target_cfg.get('mlflow_registry', True),
            cache_ttl_seconds=target_cfg.get('cache_ttl_seconds', 300)
        )
        
        # Register
        console.print(f"Registering model artifact: {model_path}")
        
        # Load validation data for signature inference if available
        validation_data = None
        try:
            datasets = model_config.get('datasets', {})
            if 'validation' in datasets:
                val_config = datasets['validation']
                val_type = val_config.get('type', 'csv')
                val_path = val_config.get('path')
                
                if val_path and val_type == 'csv':
                    from pathlib import Path
                    import pandas as pd
                    
                    # Make path absolute relative to project root
                    if not Path(val_path).is_absolute():
                        val_path = str(project.root_path / val_path)
                    
                    if Path(val_path).exists():
                        validation_data = pd.read_csv(val_path)
                        console.print(f"Loaded validation data for signature: {val_path}")
        except Exception as e:
            console.print(f"[yellow]Could not load validation data: {e}[/yellow]")
        
        try:
            entry = backend.register_model(
                name=model_name,
                source_uri=model_path,
                validation_data=validation_data,
                metadata={
                    'version': version or model_info.get('version'),
                    'risk_tier': model_info.get('risk_tier'),
                    'owner': model_info.get('owner'),
                    'use_case': model_info.get('use_case')
                }
            )
        except Exception as e:
            console.print(f"[red] Model registration failed![/red]")
            console.print(f"[red]Error: {e}[/red]")
            raise typer.Exit(1)
        
        console.print("[green] Model published successfully![/green]")
        console.print(f"\nRegistered as: [bold]{entry.get('name')}[/bold]")
        
        if entry.get('mlflow'):
            mlflow_info = entry['mlflow']
            console.print(f"MLflow Model URI: {mlflow_info.get('model_uri')}")
            if mlflow_info.get('registry_ref'):
                console.print(f"Registry Version: {mlflow_info.get('registry_ref')}")
        else:
            console.print("[yellow]Note: MLflow registration was not performed[/yellow]")
        
        console.print("\nNext steps:")
        console.print("  1. View in Databricks MLflow: Models > Registered Models")
        console.print("  2. Reference in other projects using catalog URIs")
        console.print(f"  3. Run: mrm catalog resolve databricks_uc://{model_name}")
        
    except FileNotFoundError as e:
        console.print(f" {e}", style="red")
        raise typer.Exit(1)
    except Exception as e:
        console.print(f" Error publishing model: {e}", style="red")
        import traceback
        traceback.print_exc()
        raise typer.Exit(1)


@app.command()
def version():
    """Show MRM version"""
    from mrm import __version__
    console.print(f"MRM Core version: {__version__}")


@app.command()
def doctor():
    """Print a capability report of installed backends.

    Tells the user which optional integrations the current Python env
    can actually use. Designed for air-gapped bank VDIs where pip
    extras may be partially installed.
    """
    from mrm.drift import available_backends, list_detectors
    from mrm.evidence.sign import list_signers

    # --- Drift detectors ----------------------------------------------
    console.print("\n[bold cyan]Drift detection[/bold cyan]")
    drift_table = Table(show_header=True, header_style="bold magenta")
    drift_table.add_column("Detector")
    drift_table.add_column("Backend")
    drift_table.add_column("Kind")
    drift_table.add_column("Installed")
    for entry in list_detectors():
        drift_table.add_row(
            entry["name"],
            entry["backend"],
            entry["kind"],
            "yes" if entry.get("installed") else "no",
        )
    console.print(drift_table)
    console.print(
        f"[dim]Installed backends: {', '.join(available_backends())}.[/dim]"
    )
    if "frouros" not in available_backends():
        console.print(
            "[yellow]Tip:[/yellow] install the optional drift extras with "
            r"[bold]pip install 'mrm-core\[drift]'[/bold] to enable frouros-backed "
            "detectors."
        )

    # --- Evidence signers ---------------------------------------------
    console.print("\n[bold cyan]Evidence signers[/bold cyan]")
    sig_table = Table(show_header=True, header_style="bold magenta")
    sig_table.add_column("Name")
    sig_table.add_column("Requires HSM?")
    sig_table.add_column("Tier")
    for name, meta in list_signers().items():
        tier = "paid (<brand> Cloud)" if meta["requires_hsm"] else "OSS"
        sig_table.add_row(name, "yes" if meta["requires_hsm"] else "no", tier)
    console.print(sig_table)


def _display_test_results(results: Dict):
    """Display test results in a table"""
    table = Table(title="Test Results", show_header=True, header_style="bold magenta")
    
    table.add_column("Model", style="cyan", no_wrap=True)
    table.add_column("Status", style="bold")
    table.add_column("Tests", justify="right")
    table.add_column("Passed", justify="right", style="green")
    table.add_column("Failed", justify="right", style="red")
    
    for model_name, result in results.items():
        if 'error' in result:
            table.add_row(
                model_name,
                "[red]ERROR[/red]",
                "-", "-", "-"
            )
        else:
            tests_run = result.get('tests_run', 0)
            tests_passed = result.get('tests_passed', 0)
            tests_failed = result.get('tests_failed', 0)
            
            if result['all_passed']:
                status = "[green] PASSED[/green]"
            else:
                status = "[red] FAILED[/red]"
            
            table.add_row(
                model_name,
                status,
                str(tests_run),
                str(tests_passed),
                str(tests_failed)
            )
    
    console.print(table)


# ----- Catalog subcommands -----
catalog_app = typer.Typer(help="Manage external model catalogs")
app.add_typer(catalog_app, name="catalog")


@catalog_app.command("resolve")
def catalog_resolve(
    uri: str = typer.Argument(..., help="Catalog URI, e.g. databricks_uc://catalog.schema/model_name"),
    profile: str = typer.Option("dev", "--profile", "-p", help="Profile to use")
):
    """Resolve a catalog URI to a model entry"""
    try:
        project = Project.load(profile=profile)
        catalogs = project.config.get('catalogs', {})

        # Find first databricks_unity catalog configuration
        cfg_name = None
        for k, v in catalogs.items():
            if v.get('type') in ('databricks_unity', 'databricks_uc'):
                cfg_name = k
                cfg = v
                break

        if cfg_name is None:
            console.print("No Databricks Unity Catalog configured in project (catalogs section)", style="red")
            raise typer.Exit(1)

        from mrm.core.catalog_backends.databricks_unity import DatabricksUnityCatalog

        backend = DatabricksUnityCatalog(
            host=cfg.get('host'),
            token=cfg.get('token'),
            catalog=cfg.get('catalog'),
            schema=cfg.get('schema'),
            mlflow_registry=cfg.get('mlflow_registry', True),
            cache_ttl_seconds=cfg.get('cache_ttl_seconds', 300)
        )

        # parse uri like databricks_uc://catalog.schema/name or databricks_uc://catalog/schema/name
        if uri.startswith('databricks_uc://') or uri.startswith('databricks_unity://'):
            tail = uri.split('://', 1)[1]
            # support both separators
            if '/' in tail:
                catalog_schema, name = tail.rsplit('/', 1)
            elif '.' in tail:
                catalog_schema, name = tail.rsplit('.', 1)
            else:
                catalog_schema = cfg.get('catalog') or ''
                name = tail

            if '.' in catalog_schema:
                catalog, schema = catalog_schema.split('.', 1)
            elif '/' in catalog_schema:
                parts = catalog_schema.split('/')
                catalog = parts[0]
                schema = parts[1] if len(parts) > 1 else None
            else:
                catalog = catalog_schema or cfg.get('catalog')
                schema = cfg.get('schema')

            entry = backend.get_model_entry(name, catalog=catalog, schema=schema)
            if entry is None:
                console.print(f"Model not found: {name}", style="yellow")
                raise typer.Exit(1)

            import json
            console.print(json.dumps(entry, indent=2))
        else:
            console.print("Only databricks_uc:// URIs are supported by this command", style="red")
            raise typer.Exit(1)

    except Exception as e:
        console.print(f" Error resolving catalog URI: {e}", style="red")
        raise typer.Exit(1)


@catalog_app.command("add")
def catalog_add(
    name: str = typer.Option(..., '--name', '-n', help='Model name to register'),
    from_file: str = typer.Option(..., '--from-file', '-f', help='Path to model artifact file'),
    catalog: str = typer.Option(None, '--catalog', help='Catalog key from project config'),
    profile: str = typer.Option('dev', '--profile', '-p', help='Profile to use')
):
    """Register a model pointer into the configured Databricks Unity Catalog (scaffold + MLflow register if enabled)"""
    try:
        project = Project.load(profile=profile)
        catalogs = project.config.get('catalogs', {})

        if not catalogs:
            console.print("No catalogs configured in project", style="red")
            raise typer.Exit(1)

        # choose specified catalog key or first databricks_unity
        cfg = None
        if catalog:
            cfg = catalogs.get(catalog)
            if cfg is None:
                console.print(f"Catalog key not found: {catalog}", style="red")
                raise typer.Exit(1)
        else:
            for k, v in catalogs.items():
                if v.get('type') in ('databricks_unity', 'databricks_uc'):
                    cfg = v
                    break

        if cfg is None:
            console.print("No Databricks Unity Catalog configured in project", style="red")
            raise typer.Exit(1)

        from mrm.core.catalog_backends.databricks_unity import DatabricksUnityCatalog

        backend = DatabricksUnityCatalog(
            host=cfg.get('host'),
            token=cfg.get('token'),
            catalog=cfg.get('catalog'),
            schema=cfg.get('schema'),
            mlflow_registry=cfg.get('mlflow_registry', True),
            cache_ttl_seconds=cfg.get('cache_ttl_seconds', 300)
        )

        if not from_file or not name:
            console.print("--name and --from-file are required", style="red")
            raise typer.Exit(1)

        entry = backend.register_model(name=name, source_uri=from_file)
        import json
        console.print(json.dumps(entry, indent=2))

    except Exception as e:
        console.print(f" Error registering model: {e}", style="red")
        raise typer.Exit(1)


@catalog_app.command("refresh")
def catalog_refresh(
    catalog: str = typer.Option(None, '--catalog', help='Catalog key from project config'),
    profile: str = typer.Option('dev', '--profile', '-p', help='Profile to use')
):
    """Refresh cached catalog listings"""
    try:
        project = Project.load(profile=profile)
        catalogs = project.config.get('catalogs', {})

        if not catalogs:
            console.print("No catalogs configured in project", style="red")
            raise typer.Exit(1)

        cfg = None
        if catalog:
            cfg = catalogs.get(catalog)
        else:
            for k, v in catalogs.items():
                if v.get('type') in ('databricks_unity', 'databricks_uc'):
                    cfg = v
                    break

        if cfg is None:
            console.print("No Databricks Unity Catalog configured in project", style="red")
            raise typer.Exit(1)

        from mrm.core.catalog_backends.databricks_unity import DatabricksUnityCatalog
        backend = DatabricksUnityCatalog(
            host=cfg.get('host'),
            token=cfg.get('token'),
            catalog=cfg.get('catalog'),
            schema=cfg.get('schema'),
            mlflow_registry=cfg.get('mlflow_registry', True),
            cache_ttl_seconds=cfg.get('cache_ttl_seconds', 300)
        )

        backend.refresh()
        console.print("Catalog cache refreshed", style="green")

    except Exception as e:
        console.print(f" Error refreshing catalog: {e}", style="red")
        raise typer.Exit(1)


def _display_models_table(models: List):
    """Display models in a table"""
    if not models:
        console.print("No models found", style="yellow")
        return
    
    table = Table(title="Models", show_header=True, header_style="bold magenta")
    
    table.add_column("Name", style="cyan")
    table.add_column("Version")
    table.add_column("Risk Tier")
    table.add_column("Owner")
    table.add_column("File")
    
    for model_config in models:
        model = model_config['model']
        table.add_row(
            model.get('name', '-'),
            model.get('version', '-'),
            model.get('risk_tier', '-'),
            model.get('owner', '-'),
            model_config.get('_file_path', '-')
        )
    
    console.print(table)


def _display_tests_table(tests: List):
    """Display tests in a table"""
    if not tests:
        console.print("No tests found", style="yellow")
        return
    
    table = Table(title=f"Available Tests ({len(tests)})", show_header=True)
    
    table.add_column("Test Name", style="cyan")
    table.add_column("Category")
    
    for test_name in tests:
        try:
            test_class = registry.get(test_name)
            table.add_row(test_name, test_class.category)
        except:
            table.add_row(test_name, "-")
    
    console.print(table)


def _display_suites_table(suites: Dict):
    """Display test suites in a table"""
    if not suites:
        console.print("No test suites defined", style="yellow")
        return
    
    table = Table(title="Test Suites", show_header=True)
    
    table.add_column("Suite Name", style="cyan")
    table.add_column("Tests", justify="right")
    
    for suite_name, tests in suites.items():
        table.add_row(suite_name, str(len(tests)))
    
    console.print(table)


def _display_backends_table(backends: Dict):
    """Display backends in a table"""
    if not backends:
        console.print("No backends configured", style="yellow")
        return
    
    table = Table(title="Backends", show_header=True)
    
    table.add_column("Name", style="cyan")
    table.add_column("Type")
    
    for name, config in backends.items():
        backend_type = config.get('type', 'unknown')
        table.add_row(name, backend_type)
    
    console.print(table)


def _display_crosswalk_table(items: List[Dict], from_std: Optional[str], to_std: Optional[str], show_all: bool):
    """Display crosswalk in a rich table format"""
    
    # Build title
    if show_all:
        title = "Cross-Standard Compliance Crosswalk (All Mappings)"
    elif from_std and to_std:
        title = f"Crosswalk: {from_std.upper()} → {to_std.upper()}"
    elif from_std:
        title = f"Crosswalk from {from_std.upper()}"
    elif to_std:
        title = f"Crosswalk to {to_std.upper()}"
    else:
        title = "Compliance Crosswalk"
    
    table = Table(title=title, show_header=True, header_style="bold")
    table.add_column("Concept", style="cyan", width=30)
    
    if show_all or not (from_std and to_std):
        # Show all four standards
        table.add_column("CPS 230 (AU)", width=15)
        table.add_column("SR 11-7 (US)", width=15)
        table.add_column("EU AI Act (EU)", width=15)
        table.add_column("OSFI E-23 (CA)", width=15)
    else:
        # Show only from and to
        table.add_column(f"{from_std.upper()}", width=20)
        table.add_column(f"{to_std.upper()}", width=20)
        table.add_column("Notes", width=40)
    
    for item in items:
        concept_name = item['concept']
        mappings = item['mappings']
        notes = item.get('notes', '')
        
        if show_all or not (from_std and to_std):
            # Display all four columns
            cps230_refs = '\n'.join(mappings.get('cps230', [])) or '[dim]—[/dim]'
            sr117_refs = '\n'.join(mappings.get('sr117', [])) or '[dim]—[/dim]'
            euaiact_refs = '\n'.join(mappings.get('euaiact', [])) or '[dim]—[/dim]'
            osfie23_refs = '\n'.join(mappings.get('osfie23', [])) or '[dim]—[/dim]'
            
            table.add_row(
                concept_name,
                cps230_refs,
                sr117_refs,
                euaiact_refs,
                osfie23_refs
            )
        else:
            # Display from -> to with notes
            from_refs = '\n'.join(mappings.get(from_std, [])) or '[dim]—[/dim]'
            to_refs = '\n'.join(mappings.get(to_std, [])) or '[dim]—[/dim]'
            
            # Truncate notes if too long
            if len(notes) > 100:
                notes = notes[:97] + "..."
            
            table.add_row(concept_name, from_refs, to_refs, notes)
    
    console.print(table)
    console.print(f"\n[dim]Total concepts: {len(items)}[/dim]")


def _display_crosswalk_markdown(items: List[Dict], from_std: Optional[str], to_std: Optional[str], metadata: Dict, show_all: bool):
    """Display crosswalk in markdown format suitable for documentation"""
    
    # Print title and metadata
    print("# Cross-Standard Compliance Crosswalk\n")
    print(f"**Version:** {metadata.get('version', 'unknown')}  ")
    print(f"**Created:** {metadata.get('created', 'unknown')}  ")
    print(f"**Concepts Mapped:** {metadata.get('concepts_mapped', len(items))}  \n")
    
    print("## Standards Covered\n")
    for std in metadata.get('standards_covered', []):
        print(f"- **{std['name']}** ({std['jurisdiction']}): {std['full_name']} — {std['version']}")
    
    print("\n## Mappings\n")
    
    if show_all or not (from_std and to_std):
        # Full table with all four standards
        print("| Concept | CPS 230 (AU) | SR 11-7 (US) | EU AI Act (EU) | OSFI E-23 (CA) |")
        print("|---------|--------------|--------------|----------------|----------------|")
        
        for item in items:
            concept_name = item['concept']
            mappings = item['mappings']
            
            cps230_refs = '<br>'.join(mappings.get('cps230', [])) or '—'
            sr117_refs = '<br>'.join(mappings.get('sr117', [])) or '—'
            euaiact_refs = '<br>'.join(mappings.get('euaiact', [])) or '—'
            osfie23_refs = '<br>'.join(mappings.get('osfie23', [])) or '—'
            
            print(f"| {concept_name} | {cps230_refs} | {sr117_refs} | {euaiact_refs} | {osfie23_refs} |")
    
    else:
        # Two-column from -> to with descriptions
        print(f"### {from_std.upper()} → {to_std.upper()}\n")
        print(f"| Concept | {from_std.upper()} | {to_std.upper()} | Notes |")
        print("|---------|" + "-" * (len(from_std) + 3) + "|" + "-" * (len(to_std) + 3) + "|-------|")
        
        for item in items:
            concept_name = item['concept']
            mappings = item['mappings']
            notes = item.get('notes', '')
            
            from_refs = '<br>'.join(mappings.get(from_std, [])) or '—'
            to_refs = '<br>'.join(mappings.get(to_std, [])) or '—'
            
            print(f"| {concept_name} | {from_refs} | {to_refs} | {notes} |")
    
    # Print footer notes
    notes_text = metadata.get('notes', '')
    if notes_text:
        print("\n## Notes\n")
        print(notes_text)


# ----- Docs subcommand (dbt-style) -----

docs_app = typer.Typer(help="Generate documentation and compliance reports")
app.add_typer(docs_app, name="docs")


@docs_app.command("generate")
def docs_generate(
    model: str = typer.Argument(None, help="Model name"),
    select: str = typer.Option(None, "--select", "-s", help="Model selection criteria"),
    compliance: str = typer.Option(
        None, "--compliance", "-c",
        help="Compliance standard, e.g. standard:cps230"
    ),
    format: str = typer.Option("markdown", "--format", "-f", help="Output format"),
    output: str = typer.Option(None, "--output", "-o", help="Output file path"),
    profile: str = typer.Option("dev", "--profile", "-p", help="Profile to use"),
):
    """Generate documentation, optionally with compliance reporting.

    Examples:

        mrm docs generate ccr_monte_carlo --compliance standard:cps230

        mrm docs generate --select ccr_monte_carlo --compliance standard:sr117

        mrm docs generate ccr_monte_carlo -c standard:cps230 -o report.md
    """
    try:
        project = Project.load(profile=profile)

        # Support both positional model argument and --select option
        model_selector = model or select
        if not model_selector:
            console.print(
                "Error: Must specify a model either as an argument or via --select",
                style="red"
            )
            console.print("\nExamples:")
            console.print("  mrm docs generate ccr_monte_carlo --compliance standard:cps230")
            console.print("  mrm docs generate --select ccr_monte_carlo --compliance standard:sr117")
            raise typer.Exit(1)

        model_configs = project.select_models(models=model_selector, select=select if not model else None)
        if not model_configs:
            console.print(f"Model not found: {model}", style="red")
            raise typer.Exit(1)

        model_config = model_configs[0]
        model_name = model_config['model']['name']

        if not compliance:
            console.print(f"Model: [bold]{model_name}[/bold]")
            console.print("No --compliance flag; basic docs only.")
            console.print("Use --compliance standard:<name> for compliance reports.")
            return

        # Parse standard:<name> syntax
        if ":" in compliance:
            prefix, standard_name = compliance.split(":", 1)
            if prefix != "standard":
                console.print(
                    f"Invalid compliance format '{compliance}'. "
                    "Use standard:<name> (e.g. standard:cps230)",
                    style="red",
                )
                raise typer.Exit(1)
        else:
            standard_name = compliance

        console.print(
            f"Generating compliance report ({standard_name}) "
            f"for: [bold]{model_name}[/bold]\n"
        )

        # Run tests
        runner = TestRunner(project.config, project.backend, project.catalog)
        results = runner.run_tests([model_config])
        model_results = results.get(model_name, {})
        test_results = model_results.get('test_results', {})

        # Evaluate triggers
        trigger_events = []
        triggers_cfg = model_config.get('triggers', [])
        if triggers_cfg:
            from mrm.core.triggers import ValidationTriggerEngine
            trigger_engine = ValidationTriggerEngine()
            events = trigger_engine.evaluate(
                model_name=model_name,
                trigger_configs=triggers_cfg,
                test_results=test_results,
            )
            trigger_events = [e.to_dict() for e in events]

        # Generate compliance report via the generic entry point
        from mrm.compliance.report_generator import generate_compliance_report

        output_path = (
            Path(output) if output
            else Path(f"reports/{model_name}_{standard_name}_report.md")
        )

        report_text = generate_compliance_report(
            standard_name=standard_name,
            model_name=model_name,
            model_config=model_config,
            test_results=test_results,
            trigger_events=trigger_events,
            output_path=output_path,
        )

        console.print(f"[green]Report generated: {output_path}[/green]")
        console.print(f"Report size: {len(report_text)} characters")

        _display_test_results(results)

        if trigger_events:
            console.print(f"\n[yellow]{len(trigger_events)} trigger(s) fired[/yellow]")
            for te in trigger_events:
                console.print(f"  - [{te['trigger_type']}] {te['reason']}")

    except FileNotFoundError as e:
        console.print(f" {e}", style="red")
        raise typer.Exit(1)
    except typer.Exit:
        # Re-raise typer.Exit to allow clean exit
        raise
    except Exception as e:
        console.print(f" Error generating report: {e}", style="red")
        import traceback
        traceback.print_exc()
        raise typer.Exit(1)


@docs_app.command("list-standards")
def docs_list_standards():
    """List available compliance standards"""
    from mrm.compliance.registry import compliance_registry
    compliance_registry.load_builtin_standards()
    standards = compliance_registry.list_standards()

    if not standards:
        console.print("No compliance standards available", style="yellow")
        return

    table = Table(title="Available Compliance Standards", show_header=True)
    table.add_column("Name", style="cyan")
    table.add_column("Display Name")
    table.add_column("Jurisdiction")
    table.add_column("Version")

    for name in standards:
        cls = compliance_registry.get(name)
        table.add_row(name, cls.display_name, cls.jurisdiction, cls.version)

    console.print(table)


@docs_app.command("crosswalk")
def docs_crosswalk(
    from_std: str = typer.Option(None, "--from", help="Source standard (e.g. cps230)"),
    to_std: str = typer.Option(None, "--to", help="Target standard (e.g. sr117)"),
    concept: str = typer.Option(None, "--concept", help="Filter by concept name"),
    show_all: bool = typer.Option(False, "--all", help="Show all mappings (full crosswalk matrix)"),
    format: str = typer.Option("table", "--format", "-f", help="Output format: table or markdown")
):
    """Display cross-standard compliance crosswalk

    Examples:

        mrm docs crosswalk --from cps230 --to sr117

        mrm docs crosswalk --from euaiact --to osfie23 --concept "Validation"

        mrm docs crosswalk --all

        mrm docs crosswalk --all --format markdown > crosswalk.md
    """
    import yaml
    from pathlib import Path as PathLib
    
    try:
        # Load crosswalk YAML
        crosswalk_path = PathLib(__file__).parent.parent / "compliance" / "crosswalks" / "standards.yaml"
        
        if not crosswalk_path.exists():
            console.print(f"Crosswalk file not found: {crosswalk_path}", style="red")
            raise typer.Exit(1)
        
        with open(crosswalk_path, 'r') as f:
            data = yaml.safe_load(f)
        
        crosswalk_items = data.get('crosswalk', [])
        metadata = data.get('metadata', {})
        
        if not crosswalk_items:
            console.print("No crosswalk data found", style="yellow")
            raise typer.Exit(1)
        
        # Standard name mapping (handle both short and display names)
        standard_map = {
            'cps230': 'cps230',
            'sr117': 'sr117',
            'sr11-7': 'sr117',
            'euaiact': 'euaiact',
            'eu_ai_act': 'euaiact',
            'osfie23': 'osfie23',
            'osfi_e23': 'osfie23',
        }
        
        # Normalize standard names
        from_std_normalized = standard_map.get(from_std.lower(), from_std.lower()) if from_std else None
        to_std_normalized = standard_map.get(to_std.lower(), to_std.lower()) if to_std else None
        
        # Filter items
        filtered_items = crosswalk_items
        
        if concept:
            concept_lower = concept.lower()
            filtered_items = [
                item for item in filtered_items
                if concept_lower in item['concept'].lower() or 
                   concept_lower in item.get('description', '').lower()
            ]
        
        if from_std and not show_all:
            # Filter to items that have mappings in source standard
            filtered_items = [
                item for item in filtered_items
                if item['mappings'].get(from_std_normalized, [])
            ]
        
        if to_std and not show_all:
            # Filter to items that have mappings in target standard
            filtered_items = [
                item for item in filtered_items
                if item['mappings'].get(to_std_normalized, [])
            ]
        
        if not filtered_items:
            console.print("No matching mappings found", style="yellow")
            raise typer.Exit(1)
        
        # Display results
        if format == "markdown":
            _display_crosswalk_markdown(
                filtered_items,
                from_std_normalized,
                to_std_normalized,
                metadata,
                show_all
            )
        else:
            _display_crosswalk_table(
                filtered_items,
                from_std_normalized,
                to_std_normalized,
                show_all
            )
        
        # Display metadata footer
        if not show_all:
            console.print(f"\n[dim]Crosswalk version: {metadata.get('version', 'unknown')}[/dim]")
            console.print(f"[dim]Concepts mapped: {metadata.get('concepts_mapped', len(crosswalk_items))}[/dim]")
    
    except FileNotFoundError as e:
        console.print(f" {e}", style="red")
        raise typer.Exit(1)
    except Exception as e:
        console.print(f" Error loading crosswalk: {e}", style="red")
        import traceback
        traceback.print_exc()
        raise typer.Exit(1)


@app.command(deprecated=True)
def report(
    model: str = typer.Argument(..., help="Model name"),
    format: str = typer.Option("markdown", "--format", "-f", help="Report format"),
    output: str = typer.Option(None, "--output", "-o", help="Output file path"),
    profile: str = typer.Option("dev", "--profile", "-p", help="Profile to use"),
):
    """[DEPRECATED] Use 'mrm docs generate --compliance standard:cps230' instead"""
    import warnings
    warnings.warn(
        "The 'report' command is deprecated. "
        "Use 'mrm docs generate <model> --compliance standard:cps230' instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    console.print(
        "[yellow]DEPRECATED: Use 'mrm docs generate <model> "
        "--compliance standard:cps230' instead[/yellow]\n"
    )
    docs_generate(
        model=model, compliance="standard:cps230",
        format=format, output=output, profile=profile,
    )


# ----- Triggers subcommand -----
triggers_app = typer.Typer(help="Manage validation triggers")
app.add_typer(triggers_app, name="triggers")


@triggers_app.command("check")
def triggers_check(
    model: str = typer.Argument(..., help="Model name to check triggers for"),
    profile: str = typer.Option("dev", "--profile", "-p", help="Profile to use"),
):
    """Evaluate validation triggers for a model"""
    try:
        project = Project.load(profile=profile)
        model_configs = project.select_models(models=model)

        if not model_configs:
            console.print(f"Model not found: {model}", style="red")
            raise typer.Exit(1)

        model_config = model_configs[0]
        model_name = model_config['model']['name']
        triggers_cfg = model_config.get('triggers', [])

        if not triggers_cfg:
            console.print(f"No triggers configured for {model_name}", style="yellow")
            raise typer.Exit(0)

        from mrm.core.triggers import ValidationTriggerEngine
        engine = ValidationTriggerEngine()
        events = engine.evaluate(model_name=model_name, trigger_configs=triggers_cfg)

        if events:
            table = Table(title=f"Fired Triggers - {model_name}", show_header=True)
            table.add_column("ID", style="cyan")
            table.add_column("Type")
            table.add_column("Reason")
            table.add_column("Compliance Ref")
            table.add_column("Status")

            for e in events:
                table.add_row(
                    e.trigger_id,
                    e.trigger_type.value,
                    e.reason,
                    e.compliance_reference,
                    e.status.value,
                )
            console.print(table)
        else:
            console.print(f"[green]No triggers fired for {model_name}[/green]")

    except Exception as e:
        console.print(f" Error checking triggers: {e}", style="red")
        raise typer.Exit(1)


@triggers_app.command("list")
def triggers_list(
    model: str = typer.Option(None, "--model", "-m", help="Filter by model name"),
):
    """List all trigger events"""
    try:
        from mrm.core.triggers import ValidationTriggerEngine
        engine = ValidationTriggerEngine()
        events = engine.get_all_events(model_name=model)

        if not events:
            console.print("No trigger events found", style="yellow")
            raise typer.Exit(0)

        table = Table(title="Trigger Events", show_header=True)
        table.add_column("ID", style="cyan")
        table.add_column("Model")
        table.add_column("Type")
        table.add_column("Fired At")
        table.add_column("Reason")
        table.add_column("Status")

        for e in events:
            status_style = {
                "fired": "red",
                "acknowledged": "yellow",
                "resolved": "green",
            }.get(e.status.value, "white")

            table.add_row(
                e.trigger_id,
                e.model_name,
                e.trigger_type.value,
                e.fired_at[:19],
                e.reason[:50],
                f"[{status_style}]{e.status.value}[/{status_style}]",
            )
        console.print(table)

    except Exception as e:
        console.print(f" Error listing triggers: {e}", style="red")
        raise typer.Exit(1)


@triggers_app.command("resolve")
def triggers_resolve(
    model: str = typer.Argument(..., help="Model name to resolve triggers for"),
):
    """Resolve all active triggers for a model (after re-validation)"""
    try:
        from mrm.core.triggers import ValidationTriggerEngine
        engine = ValidationTriggerEngine()
        active = engine.get_active_triggers(model_name=model)

        if not active:
            console.print(f"No active triggers for {model}", style="green")
            raise typer.Exit(0)

        engine.resolve_model(model)
        console.print(
            f"[green]Resolved {len(active)} trigger(s) for {model}[/green]"
        )

    except Exception as e:
        console.print(f" Error resolving triggers: {e}", style="red")
        raise typer.Exit(1)


# ----- Evidence subcommand -----
evidence_app = typer.Typer(help="Manage immutable evidence vault")
app.add_typer(evidence_app, name="evidence")


@evidence_app.command("freeze")
def evidence_freeze(
    model: str = typer.Argument(..., help="Model name to create evidence for"),
    backend: Optional[str] = typer.Option(None, "--backend", "-b", help="Override default_evidence type (local | s3)"),
    bucket: Optional[str] = typer.Option(None, "--bucket", help="Override S3 bucket"),
    retention: Optional[int] = typer.Option(None, "--retention", "-r", help="Override retention period (days)"),
    created_by: Optional[str] = typer.Option(None, "--created-by", help="User identifier (email)"),
    profile: str = typer.Option("dev", "--profile", "-p", help="Active target (profiles.yml outputs.<target>)"),
):
    """Freeze validation results as immutable evidence packet
    
    Examples:
    
        mrm evidence freeze ccr_monte_carlo --backend local
        
        mrm evidence freeze ccr_monte_carlo --backend s3 --bucket my-evidence --retention 2555
    """
    try:
        from pathlib import Path as PathLib
        import getpass
        import os
        from mrm.evidence.packet import EvidencePacket
        from mrm.evidence.backends.local import LocalFilesystemBackend
        
        # Load project
        project = Project.load(profile=profile)
        model_configs = project.select_models(models=model)
        
        if not model_configs:
            console.print(f"Model not found: {model}", style="red")
            raise typer.Exit(1)
        
        model_config = model_configs[0]
        model_name = model_config['model']['name']
        model_info = model_config['model']
        
        console.print(f"Creating evidence packet for: [bold]{model_name}[/bold]")
        
        # Get model artifact path
        location = model_info.get('location', {})
        if isinstance(location, str):
            if location.startswith('file/'):
                model_path = location[5:]
            else:
                model_path = location
        else:
            model_path = location.get('path')
        
        if not model_path:
            console.print("Model location/path not found in config", style="red")
            raise typer.Exit(1)
        
        # Make path absolute
        if not PathLib(model_path).is_absolute():
            model_path = str(project.root_path / model_path)
        
        model_artifact = PathLib(model_path)
        if not model_artifact.exists():
            console.print(f"Model artifact not found: {model_path}", style="red")
            raise typer.Exit(1)
        
        # Run tests to get current results
        console.print("Running validation tests...")
        from mrm.engine.runner import TestRunner
        runner = TestRunner(project.config, project.backend, project.catalog)
        results = runner.run_tests([model_config])
        
        model_results = results.get(model_name, {})
        test_results_raw = model_results.get('test_results', {})
        
        if not test_results_raw:
            console.print("No test results available", style="yellow")
            raise typer.Exit(1)
        
        # Convert TestResult objects to dicts
        test_results = {}
        for test_name, test_result in test_results_raw.items():
            if hasattr(test_result, 'to_dict'):
                test_results[test_name] = test_result.to_dict()
            else:
                test_results[test_name] = test_result
        
        # Get compliance mappings (these come from the model config)
        # For now, we'll extract from model's configured tests
        compliance_mappings = {}
        tests_cfg = model_config.get('tests', [])
        for test_cfg in tests_cfg:
            if isinstance(test_cfg, dict):
                test_compliance = test_cfg.get('compliance', {})
                for standard, paragraphs in test_compliance.items():
                    if standard not in compliance_mappings:
                        compliance_mappings[standard] = []
                    if isinstance(paragraphs, list):
                        compliance_mappings[standard].extend(paragraphs)
                    else:
                        compliance_mappings[standard].append(paragraphs)
        
        # Get created_by (user email/username)
        if not created_by:
            created_by = os.environ.get('USER', getpass.getuser())
        
        # Resolve backend via project+profile+env+CLI override chain.
        backend_impl, resolved_type, resolved_cfg = _build_evidence_backend(
            project,
            backend_flag=backend,
            bucket_flag=bucket,
            retention_flag=retention,
        )
        console.print(
            f"[dim]Resolved default_evidence: type={resolved_type}"
            + (f", bucket={resolved_cfg.get('bucket')}" if resolved_cfg.get('bucket') else "")
            + "[/dim]"
        )

        # Get prior packet (for hash chain)
        prior_packet = backend_impl.get_latest_packet(model_name)
        
        # Create evidence packet
        console.print("Creating evidence packet...")
        packet = EvidencePacket.create(
            model_name=model_name,
            model_version=model_info.get('version', '1.0'),
            model_artifact_path=model_artifact,
            test_results=test_results,
            compliance_mappings=compliance_mappings,
            created_by=created_by,
            prior_packet=prior_packet,
            metadata={
                'profile': profile,
                'risk_tier': model_info.get('risk_tier'),
                'owner': model_info.get('owner')
            }
        )
        
        # Verify packet before freezing
        if not packet.verify_hash():
            console.print("Packet hash verification failed", style="red")
            raise typer.Exit(1)
        
        # Freeze packet
        console.print(f"Freezing packet with {backend} backend...")
        uri = backend_impl.freeze(packet, retention_days=retention)
        
        console.print(f"\n[green]✓ Evidence packet frozen successfully[/green]")
        console.print(f"  Packet ID: {packet.packet_id}")
        console.print(f"  URI: {uri}")
        console.print(f"  Content Hash: {packet.content_hash}")
        console.print(f"  Model Artifact Hash: {packet.model_artifact_hash}")
        
        if prior_packet:
            console.print(f"  Prior Packet: {prior_packet.packet_id}")
            console.print(f"  Chain Length: {len(backend_impl.list_packets(model_name=model_name))}")
        else:
            console.print(f"  [yellow]First packet in chain[/yellow]")
        
        console.print(f"\nNext steps:")
        console.print(f"  mrm evidence verify {uri}")
        console.print(f"  mrm evidence list --model {model_name}")
    
    except FileNotFoundError as e:
        console.print(f" {e}", style="red")
        raise typer.Exit(1)
    except Exception as e:
        console.print(f" Error freezing evidence: {e}", style="red")
        import traceback
        traceback.print_exc()
        raise typer.Exit(1)


@evidence_app.command("verify")
def evidence_verify(
    uri: str = typer.Argument(..., help="Evidence packet URI"),
    chain: bool = typer.Option(True, "--chain/--no-chain", help="Verify full hash chain"),
):
    """Verify evidence packet integrity and hash chain
    
    Examples:
    
        mrm evidence verify file:///path/to/packets.jsonl#packet-id
        
        mrm evidence verify s3://bucket/evidence/model/packet-id.json --chain
    """
    try:
        from pathlib import Path as PathLib
        from mrm.evidence.backends.local import LocalFilesystemBackend
        
        # Determine backend from URI
        if uri.startswith('file://'):
            # Parse path from URI
            path_part = uri[7:].split('#')[0]
            evidence_dir = PathLib(path_part).parent.parent
            backend = LocalFilesystemBackend(evidence_dir, warn_on_use=False)
            
        elif uri.startswith('s3://'):
            try:
                from mrm.evidence.backends.s3_object_lock import S3ObjectLockBackend
            except ImportError:
                console.print(
                    "S3 backend requires boto3: pip install boto3",
                    style="red"
                )
                raise typer.Exit(1)
            
            # Parse bucket from URI
            bucket = uri.split('/')[2]
            backend = S3ObjectLockBackend(bucket=bucket)
        
        else:
            console.print(f"Unknown URI scheme: {uri}", style="red")
            raise typer.Exit(1)
        
        # Verify packet
        console.print(f"Verifying: {uri}")
        result = backend.verify(uri, verify_chain=chain)
        
        if result['valid']:
            console.print(f"\n[green]✓ Verification passed[/green]")
            console.print(f"  Reason: {result['reason']}")
            
            if 'packet_count' in result:
                console.print(f"  Chain length: {result['packet_count']} packets")
                console.print(f"  First packet: {result.get('first_packet', 'N/A')}")
                console.print(f"  Latest packet: {result.get('latest_packet', 'N/A')}")
            
            if 'retention_info' in result:
                retention = result['retention_info']
                if 'error' not in retention:
                    console.print(f"  Retention mode: {retention.get('retention_mode', 'N/A')}")
                    console.print(f"  Retain until: {retention.get('retain_until', 'N/A')}")
        
        else:
            console.print(f"\n[red]✗ Verification failed[/red]")
            console.print(f"  Reason: {result['reason']}")
            
            if 'packet_id' in result:
                console.print(f"  Packet ID: {result['packet_id']}")
            
            raise typer.Exit(1)
    
    except FileNotFoundError as e:
        console.print(f" {e}", style="red")
        raise typer.Exit(1)
    except Exception as e:
        console.print(f" Error verifying evidence: {e}", style="red")
        import traceback
        traceback.print_exc()
        raise typer.Exit(1)


@evidence_app.command("list")
def evidence_list(
    model: Optional[str] = typer.Option(None, "--model", "-m", help="Filter by model name"),
    backend: Optional[str] = typer.Option(None, "--backend", "-b", help="Override default_evidence type"),
    bucket: Optional[str] = typer.Option(None, "--bucket", help="Override S3 bucket"),
    profile: str = typer.Option("dev", "--profile", "-p", help="Active target"),
):
    """List evidence packets

    Examples:

        mrm evidence list --model ccr_monte_carlo

        mrm evidence list --backend s3 --bucket my-evidence
    """
    try:
        project = Project.load(profile=profile)
        backend_impl, resolved_type, resolved_cfg = _build_evidence_backend(
            project,
            backend_flag=backend,
            bucket_flag=bucket,
            retention_flag=None,
        )

        # List packets
        packets = backend_impl.list_packets(model_name=model)
        
        if not packets:
            console.print("No evidence packets found", style="yellow")
            raise typer.Exit(0)
        
        # Display table
        table = Table(title=f"Evidence Packets ({backend})", show_header=True)
        table.add_column("Model", style="cyan")
        table.add_column("Version")
        table.add_column("Packet ID")
        table.add_column("Timestamp")
        table.add_column("Created By")
        
        for packet in packets:
            table.add_row(
                packet.get('model_name', '')[:20],
                packet.get('model_version', '')[:10],
                packet.get('packet_id', '')[:12] + '...',
                packet.get('timestamp', '')[:19],
                packet.get('created_by', '')[:20]
            )
        
        console.print(table)
        console.print(f"\n[dim]Total: {len(packets)} packet(s)[/dim]")
    
    except FileNotFoundError as e:
        console.print(f" {e}", style="red")
        raise typer.Exit(1)
    except Exception as e:
        console.print(f" Error listing evidence: {e}", style="red")
        import traceback
        traceback.print_exc()
        raise typer.Exit(1)


# ---------------------------------------------------------------------------
# `mrm evidence root` + `mrm evidence conformance`  -- Cryptographic
# hardening (P9). Daily Merkle aggregation + signer abstraction.
# ---------------------------------------------------------------------------

root_app = typer.Typer(help="Daily Merkle root publication + verification")
evidence_app.add_typer(root_app, name="root")

conformance_app = typer.Typer(help="Run the evidence-vault conformance suite")
evidence_app.add_typer(conformance_app, name="conformance")


def _build_evidence_backend(project, *, backend_flag, bucket_flag, retention_flag):
    """Resolve and build an EvidenceBackend via the project resolver.

    CLI flags act as the top precedence layer; omitted flags fall
    through to ``profiles.yml`` -> ``mrm_project.yml`` defaults.
    """
    from pathlib import Path as PathLib

    overrides: Dict[str, Any] = {}
    if backend_flag is not None:
        overrides["type"] = backend_flag
    if bucket_flag is not None:
        overrides["bucket"] = bucket_flag
    if retention_flag is not None:
        overrides["retention_days"] = retention_flag

    cfg = project.resolve_backend("default_evidence", cli_overrides=overrides)
    btype = cfg.get("type") or "local"

    if btype == "local":
        from mrm.evidence.backends.local import LocalFilesystemBackend
        evidence_dir = PathLib(cfg.get("path") or (project.root_path / "evidence"))
        return LocalFilesystemBackend(evidence_dir), btype, cfg

    if btype in ("s3", "s3_object_lock"):
        bucket = cfg.get("bucket")
        if not bucket:
            console.print(
                "default_evidence resolution missing 'bucket'. Set it via "
                "--bucket, profiles.yml outputs.<target>.backends.default_evidence.bucket, "
                "or mrm_project.yml backends.default_evidence.bucket.",
                style="red",
            )
            raise typer.Exit(1)
        try:
            from mrm.evidence.backends.s3_object_lock import S3ObjectLockBackend
        except ImportError:
            console.print("S3 backend requires boto3: pip install boto3", style="red")
            raise typer.Exit(1)
        return S3ObjectLockBackend(bucket=bucket), btype, cfg

    console.print(f"Unknown default_evidence type: {btype}", style="red")
    raise typer.Exit(1)


def _resolve_signer(signer_name: str, key_path: Optional[str]) -> "object":
    """Build a Signer from CLI flags. Returns a Signer or raises Exit."""
    from mrm.evidence.sign import build_signer

    cfg: Dict[str, Any] = {"name": signer_name}
    if signer_name == "local":
        if not key_path:
            console.print("--key-path required for local signer", style="red")
            raise typer.Exit(1)
        cfg["key_path"] = key_path
    elif signer_name == "cloud-hsm":
        console.print(
            "cloud-hsm is a paid-tier feature (see STRATEGY.md P15). "
            "OSS signers: local, gpg, age, kms.",
            style="yellow",
        )
        raise typer.Exit(2)
    try:
        return build_signer(cfg)
    except (ImportError, NotImplementedError) as exc:
        console.print(f"Signer unavailable: {exc}", style="red")
        raise typer.Exit(1)


@root_app.command("publish")
def evidence_root_publish(
    epoch: str = typer.Option(..., "--date", "-d", help="UTC date (YYYY-MM-DD)"),
    chain_dir: str = typer.Option("evidence/chain", "--chain-dir", help="HMAC-chain directory"),
    roots_dir: str = typer.Option("evidence/roots", "--roots-dir", help="Where to write signed roots"),
    signer: str = typer.Option("local", "--signer", help="local | gpg | age | kms | cloud-hsm"),
    key_path: Optional[str] = typer.Option(None, "--key-path", help="Local-signer key file"),
    chain_secret_hex: Optional[str] = typer.Option(
        None, "--chain-secret", help="Hex chain secret (overrides chain.secret on disk)"
    ),
):
    """Aggregate one UTC day's chained events into a signed Merkle root.

    Production architecture:

    * Fast path  -- HMAC-chained events accumulate during the day.
    * Lockdown   -- this command runs at midnight UTC, builds the
                    Merkle tree, calls the configured Signer (KMS or
                    HSM in production), and writes ``{epoch}.root.json``.
    """
    try:
        from mrm.evidence.merkle import aggregate_epoch, write_root

        from pathlib import Path as PathLib

        signer_obj = _resolve_signer(signer, key_path)
        secret = bytes.fromhex(chain_secret_hex) if chain_secret_hex else None
        root = aggregate_epoch(PathLib(chain_dir), epoch=epoch, chain_secret=secret)
        signed = signer_obj.sign(root)
        target = write_root(PathLib(roots_dir), signed)
        console.print(f"Published [bold]{target}[/bold]")
        console.print(f"  epoch:        {signed.epoch}")
        console.print(f"  leaf_count:   {signed.leaf_count}")
        console.print(f"  root_hash:    {signed.root_hash}")
        console.print(f"  signer:       {signed.signer}")
        console.print(f"  signature:    {(signed.signature or '')[:40]}...")
    except typer.Exit:
        raise
    except Exception as exc:
        console.print(f" Error publishing root: {exc}", style="red")
        import traceback; traceback.print_exc()
        raise typer.Exit(1)


@root_app.command("verify")
def evidence_root_verify(
    epoch: str = typer.Option(..., "--date", "-d", help="UTC date (YYYY-MM-DD)"),
    chain_dir: str = typer.Option("evidence/chain", "--chain-dir", help="HMAC-chain directory"),
    roots_dir: str = typer.Option("evidence/roots", "--roots-dir", help="Where signed roots live"),
    signer: str = typer.Option("local", "--signer", help="local | gpg | age | kms"),
    key_path: Optional[str] = typer.Option(None, "--key-path", help="Local-signer key file"),
    chain_secret_hex: Optional[str] = typer.Option(
        None, "--chain-secret", help="Hex chain secret (overrides chain.secret on disk)"
    ),
):
    """Verify a published root: signature + re-derived Merkle hash."""
    try:
        from pathlib import Path as PathLib

        from mrm.evidence.merkle import read_root, reproduce_root_from_chain

        signer_obj = _resolve_signer(signer, key_path)
        root = read_root(PathLib(roots_dir), epoch)

        # 1. Signature
        sig_ok = signer_obj.verify(root)
        # 2. Independently re-derive the Merkle root from the events.
        secret = bytes.fromhex(chain_secret_hex) if chain_secret_hex else None
        rederived = reproduce_root_from_chain(
            PathLib(chain_dir), epoch, chain_secret=secret
        )
        match = rederived == root.root_hash

        if sig_ok and match:
            console.print(f"[green]Root OK[/green] for {epoch}")
            console.print(f"  signature:  verified ({root.signer})")
            console.print(f"  rederived:  matches published root")
        else:
            console.print(f"[red]Root FAIL[/red] for {epoch}", style="red")
            console.print(f"  signature ok: {sig_ok}")
            console.print(f"  rederive ok:  {match}")
            raise typer.Exit(2)
    except typer.Exit:
        raise
    except Exception as exc:
        console.print(f" Error verifying root: {exc}", style="red")
        raise typer.Exit(1)


@root_app.command("show")
def evidence_root_show(
    epoch: str = typer.Option(..., "--date", "-d", help="UTC date (YYYY-MM-DD)"),
    roots_dir: str = typer.Option("evidence/roots", "--roots-dir", help="Where signed roots live"),
):
    """Print a published Merkle root."""
    try:
        from pathlib import Path as PathLib
        from mrm.evidence.merkle import read_root

        root = read_root(PathLib(roots_dir), epoch)
        console.print_json(root.to_json(indent=2))
    except Exception as exc:
        console.print(f" Error reading root: {exc}", style="red")
        raise typer.Exit(1)


@root_app.command("list-signers")
def evidence_root_list_signers():
    """List signer names + which require an HSM (paid-tier marker)."""
    from mrm.evidence.sign import list_signers

    table = Table(title="Evidence signers")
    table.add_column("Name", style="cyan")
    table.add_column("Requires HSM?")
    table.add_column("Tier")
    for name, meta in list_signers().items():
        tier = "paid (<brand> Cloud)" if meta["requires_hsm"] else "OSS"
        table.add_row(name, "yes" if meta["requires_hsm"] else "no", tier)
    console.print(table)


@conformance_app.command("run")
def evidence_conformance_run(
    vectors_dir: Optional[str] = typer.Option(
        None,
        "--vectors-dir",
        help="Override default test-vectors path (docs/spec/test-vectors/evidence)",
    ),
):
    """Run the evidence-vault conformance test corpus.

    Implementations claiming conformance with the v1 spec must accept
    every positive vector and reject every negative vector.
    """
    try:
        from mrm.evidence import _conformance

        results = _conformance.run_all(vectors_dir)
        for v in results["details"]:
            mark = "ok" if v["passed"] else "FAIL"
            console.print(f"  [{mark}] {v['name']} -- {v['summary']}")
        console.print(
            f"\n{results['passed']}/{results['total']} passed; "
            f"{results['failed']} failed."
        )
        if results["failed"]:
            raise typer.Exit(2)
    except typer.Exit:
        raise
    except Exception as exc:
        console.print(f" Error running conformance: {exc}", style="red")
        raise typer.Exit(1)


# ---------------------------------------------------------------------------
# `mrm replay` — 1:1 Decision Replay (P7)
# ---------------------------------------------------------------------------

replay_app = typer.Typer(help="Capture, reconstruct, and verify model decisions")
app.add_typer(replay_app, name="replay")


def _load_replay_backend(profile: str, backend: Optional[str], bucket: Optional[str] = None):
    """Resolve a replay backend via the project resolver.

    CLI flags (``backend``, ``bucket``) act as the highest precedence
    layer when non-None. Omitted flags fall through to
    ``profiles.yml`` -> ``mrm_project.yml`` defaults for the
    ``default_replay`` role.
    """
    from pathlib import Path as PathLib

    try:
        project = Project.load(profile=profile)
    except Exception:
        project = None

    overrides: Dict[str, Any] = {}
    if backend is not None:
        overrides["type"] = backend
    if bucket is not None:
        overrides["bucket"] = bucket

    if project is not None:
        cfg = project.resolve_backend("default_replay", cli_overrides=overrides)
        default_dir = project.root_path / "replay"
    else:
        cfg = overrides
        default_dir = PathLib.cwd() / "replay"

    btype = cfg.get("type") or "local"

    if btype == "local":
        from mrm.replay.backends.local import LocalReplayBackend
        replay_dir = PathLib(cfg.get("path") or default_dir)
        return LocalReplayBackend(replay_dir, warn_on_use=False)

    if btype == "s3":
        b = cfg.get("bucket")
        if not b:
            console.print(
                "default_replay resolution missing 'bucket'. Set it via --bucket, "
                "profiles.yml outputs.<target>.backends.default_replay.bucket, "
                "or mrm_project.yml backends.default_replay.bucket.",
                style="red",
            )
            raise typer.Exit(1)
        try:
            from mrm.replay.backends.s3 import S3ReplayBackend
        except ImportError:
            console.print("S3 replay backend requires boto3: pip install boto3", style="red")
            raise typer.Exit(1)
        return S3ReplayBackend(bucket=b)

    console.print(f"Unknown replay backend: {btype}", style="red")
    raise typer.Exit(1)


@replay_app.command("record")
def replay_record(
    model: str = typer.Argument(..., help="Model name to record a decision for"),
    inputs: str = typer.Option(..., "--inputs", "-i", help="Path to a CSV/JSON input file"),
    backend: Optional[str] = typer.Option(None, "--backend", "-b", help="Override default_replay type"),
    bucket: Optional[str] = typer.Option(None, "--bucket", help="S3 bucket (for s3 backend)"),
    profile: str = typer.Option("dev", "--profile", "-p", help="Profile to use"),
):
    """Run a single inference and capture it as a DecisionRecord.

    Loads the model from the project config, invokes ``.predict(...)``
    on the inputs, and appends a hash-chained replay record.
    """
    try:
        import json
        from pathlib import Path as PathLib

        import pandas as pd

        from mrm.engine.runner import TestRunner  # noqa: F401 - ensures registry load

        from mrm.replay.capture import CaptureContext
        from mrm.replay.record import ModelIdentity

        project = Project.load(profile=profile)
        model_configs = project.select_models(models=model)
        if not model_configs:
            console.print(f"Model not found: {model}", style="red")
            raise typer.Exit(1)
        model_config = model_configs[0]["model"]

        location = model_config.get("location", {})
        if isinstance(location, str):
            model_path = location[5:] if location.startswith("file/") else location
        else:
            model_path = location.get("path")

        if not model_path:
            console.print("Model location/path not found in config", style="red")
            raise typer.Exit(1)

        model_path = PathLib(model_path)
        if not model_path.is_absolute():
            model_path = project.root_path / model_path

        # Load model artifact (pickle by convention; LLM endpoints handled
        # separately via the genai adapter).
        import pickle
        with open(model_path, "rb") as fh:
            artifact = pickle.load(fh)

        # Load inputs.
        inputs_path = PathLib(inputs)
        if not inputs_path.is_absolute():
            inputs_path = project.root_path / inputs_path
        if inputs_path.suffix.lower() == ".csv":
            frame = pd.read_csv(inputs_path)
            input_state = {"features": frame.to_dict(orient="records")}
            predict_payload = frame
        else:
            with open(inputs_path, "r", encoding="utf-8") as fh:
                payload = json.load(fh)
            input_state = {"features": payload}
            predict_payload = payload

        # Best-effort artifact hash.
        from mrm.evidence.packet import EvidencePacket
        artifact_hash = EvidencePacket.compute_artifact_hash(model_path)

        identity = ModelIdentity(
            name=model_config["name"],
            version=str(model_config.get("version", "unknown")),
            uri=str(model_path),
            artifact_hash=artifact_hash,
        )

        backend_impl = _load_replay_backend(profile, backend, bucket)
        ctx = CaptureContext(backend_impl, model_identity=identity)
        prediction = artifact.predict(predict_payload) if hasattr(artifact, "predict") else artifact(predict_payload)
        # Coerce numpy/pandas outputs through the same helper as the decorator.
        from mrm.replay.capture import _to_jsonable
        record = ctx.record(input_state=input_state, output=_to_jsonable(prediction))

        console.print(f"Recorded decision [bold]{record.record_id}[/bold] for {model}")
        console.print(f"  content_hash:      {record.content_hash}")
        console.print(f"  prior_record_hash: {record.prior_record_hash or '(genesis)'}")
    except typer.Exit:
        raise
    except FileNotFoundError as e:
        console.print(f" {e}", style="red")
        raise typer.Exit(1)
    except Exception as e:
        console.print(f" Error recording decision: {e}", style="red")
        import traceback
        traceback.print_exc()
        raise typer.Exit(1)


@replay_app.command("reconstruct")
def replay_reconstruct(
    record_id: str = typer.Argument(..., help="DecisionRecord ID"),
    backend: Optional[str] = typer.Option(None, "--backend", "-b", help="Override default_replay type"),
    bucket: Optional[str] = typer.Option(None, "--bucket", help="S3 bucket (for s3 backend)"),
    profile: str = typer.Option("dev", "--profile", "-p", help="Profile to use"),
):
    """Print the captured input/output for a given record."""
    try:
        backend_impl = _load_replay_backend(profile, backend, bucket)
        record = backend_impl.get(record_id)
        if record is None:
            console.print(f"Record not found: {record_id}", style="red")
            raise typer.Exit(1)
        console.print_json(record.to_json())
    except typer.Exit:
        raise
    except Exception as e:
        console.print(f" Error reconstructing record: {e}", style="red")
        raise typer.Exit(1)


@replay_app.command("verify")
def replay_verify(
    record_id: str = typer.Argument(..., help="DecisionRecord ID"),
    tolerance: float = typer.Option(1e-9, "--tolerance", help="Numeric replay tolerance"),
    backend: Optional[str] = typer.Option(None, "--backend", "-b", help="Override default_replay type"),
    bucket: Optional[str] = typer.Option(None, "--bucket", help="S3 bucket (for s3 backend)"),
    profile: str = typer.Option("dev", "--profile", "-p", help="Profile to use"),
):
    """Re-run a recorded decision and diff against the captured output."""
    try:
        import pickle
        from pathlib import Path as PathLib

        import pandas as pd

        from mrm.replay.verify import verify as do_verify

        backend_impl = _load_replay_backend(profile, backend, bucket)
        record = backend_impl.get(record_id)
        if record is None:
            console.print(f"Record not found: {record_id}", style="red")
            raise typer.Exit(1)

        project = Project.load(profile=profile)
        model_configs = project.select_models(models=record.model_identity.name)
        if not model_configs:
            console.print(f"Model not found in project: {record.model_identity.name}", style="red")
            raise typer.Exit(1)
        model_config = model_configs[0]["model"]
        location = model_config.get("location", {})
        model_path = location[5:] if isinstance(location, str) and location.startswith("file/") else (
            location if isinstance(location, str) else location.get("path")
        )
        model_path = PathLib(model_path)
        if not model_path.is_absolute():
            model_path = project.root_path / model_path

        with open(model_path, "rb") as fh:
            artifact = pickle.load(fh)

        def predictor(features):
            if isinstance(features, list):
                features = pd.DataFrame(features)
            return artifact.predict(features).tolist() if hasattr(artifact, "predict") else artifact(features)

        diff = do_verify(record, predictor, tolerance=tolerance)
        status = "[green]MATCHED[/green]" if diff.matched else "[red]DRIFT[/red]"
        console.print(f"Replay verify: {status} (tolerance={tolerance})")
        if not diff.matched:
            console.print("Differences:")
            for d in diff.differences[:20]:
                console.print(f"  - {d}")
            raise typer.Exit(2)
    except typer.Exit:
        raise
    except Exception as e:
        console.print(f" Error verifying replay: {e}", style="red")
        import traceback
        traceback.print_exc()
        raise typer.Exit(1)


@replay_app.command("sample")
def replay_sample(
    model: Optional[str] = typer.Option(None, "--model", "-m", help="Restrict to a model"),
    since: Optional[str] = typer.Option(None, "--since", help="ISO-8601 lower bound"),
    until: Optional[str] = typer.Option(None, "--until", help="ISO-8601 upper bound"),
    n: int = typer.Option(10, "--n", help="Max records to return"),
    backend: Optional[str] = typer.Option(None, "--backend", "-b", help="Override default_replay type"),
    bucket: Optional[str] = typer.Option(None, "--bucket", help="S3 bucket (for s3 backend)"),
    profile: str = typer.Option("dev", "--profile", "-p", help="Profile to use"),
):
    """Sample-on-demand record export (regulator-portal shape)."""
    try:
        backend_impl = _load_replay_backend(profile, backend, bucket)
        records = backend_impl.sample(model_name=model, since=since, until=until, n=n)
        table = Table(title=f"Replay sample (n={len(records)})")
        table.add_column("Record ID", style="cyan")
        table.add_column("Model")
        table.add_column("Version")
        table.add_column("Timestamp")
        table.add_column("Content hash")
        for r in records:
            table.add_row(
                r.record_id[:12] + "...",
                r.model_identity.name[:20],
                r.model_identity.version[:10],
                r.timestamp[:19],
                (r.content_hash or "")[:16] + "...",
            )
        console.print(table)
    except Exception as e:
        console.print(f" Error sampling records: {e}", style="red")
        raise typer.Exit(1)


@replay_app.command("verify-chain")
def replay_verify_chain(
    model: str = typer.Argument(..., help="Model name to verify"),
    backend: Optional[str] = typer.Option(None, "--backend", "-b", help="Override default_replay type"),
    bucket: Optional[str] = typer.Option(None, "--bucket", help="S3 bucket (for s3 backend)"),
    profile: str = typer.Option("dev", "--profile", "-p", help="Profile to use"),
):
    """Walk the hash chain for a model and verify every link."""
    try:
        backend_impl = _load_replay_backend(profile, backend, bucket)
        ok = backend_impl.verify_chain(model)
        if ok:
            console.print(f"[green]Chain OK[/green] for {model}")
        else:
            console.print(f"[red]Chain BROKEN[/red] for {model}")
            raise typer.Exit(2)
    except typer.Exit:
        raise
    except Exception as e:
        console.print(f" Error verifying chain: {e}", style="red")
        raise typer.Exit(1)


# ---------------------------------------------------------------------------
# `mrm portal` — Regulator evidence export (regulator-portal-v1)
# ---------------------------------------------------------------------------

portal_app = typer.Typer(help="Self-verifying evidence export for regulators")
app.add_typer(portal_app, name="portal")


def _collect_export_data(
    project,
    model_list: List[str],
    standard_list: Optional[List[str]],
    start: str,
    end: str,
    include_decisions: bool,
    decision_sample_n: int,
    evidence_backend: Optional[str],
    evidence_bucket: Optional[str],
    replay_backend: Optional[str],
    replay_bucket: Optional[str],
    profile: str,
) -> Dict[str, Any]:
    """Gather evidence, decisions, reports, roots, and proofs.

    Returns a summary dict used by both ``--dry-run`` and the real build.
    """
    from pathlib import Path as PathLib

    summary: Dict[str, Any] = {
        "models": {},
        "merkle_roots": [],
        "inclusion_proofs": [],
        "ev_backend": None,
    }

    # --- Resolve evidence backend ---
    ev_backend = None
    try:
        ev_backend, _, _ = _build_evidence_backend(
            project,
            backend_flag=evidence_backend,
            bucket_flag=evidence_bucket,
            retention_flag=None,
        )
        summary["ev_backend"] = ev_backend
    except (typer.Exit, Exception) as exc:
        console.print(
            f"[yellow]Warning: could not load evidence backend: {exc}. "
            f"Continuing with empty evidence.[/yellow]"
        )

    all_leaf_hashes: Dict[str, list] = {}  # epoch -> [content_hash, ...]

    for model_name in model_list:
        console.print(f"Collecting evidence for [bold]{model_name}[/bold]...")
        model_data: Dict[str, Any] = {
            "packets": [],
            "decision_records": [],
            "reports": {},
        }

        # --- Evidence packets ---
        if ev_backend is not None:
            try:
                packet_metas = ev_backend.list_packets(
                    model_name=model_name,
                    start_date=start,
                    end_date=end,
                )
                # list_packets returns descending; reverse to
                # chronological so the hash chain is in append order.
                for meta in reversed(packet_metas or []):
                    uri = meta.get("uri")
                    if uri:
                        try:
                            full_packet = ev_backend.retrieve(uri)
                            model_data["packets"].append(full_packet.to_dict())
                        except Exception:
                            model_data["packets"].append(meta)
                    else:
                        model_data["packets"].append(meta)
            except Exception as exc:
                console.print(
                    f"  [yellow]Could not load packets: {exc}[/yellow]"
                )

        console.print(f"  {len(model_data['packets'])} evidence packet(s)")

        # Collect leaf hashes for inclusion proofs
        for pkt in model_data["packets"]:
            ts = pkt.get("timestamp", "")[:10]
            if ts:
                all_leaf_hashes.setdefault(ts, []).append(
                    pkt.get("content_hash", "")
                )

        # --- Decision records (sampled) ---
        if include_decisions:
            try:
                replay_be = _load_replay_backend(
                    profile, replay_backend, replay_bucket
                )
                records = replay_be.sample(
                    model_name=model_name,
                    since=start,
                    until=end,
                    n=decision_sample_n,
                )
                model_data["decision_records"] = [
                    r.model_dump(mode="json")
                    if hasattr(r, "model_dump")
                    else r.dict()
                    for r in records
                ]
                console.print(
                    f"  {len(model_data['decision_records'])} decision record(s)"
                    + (f" (sampled, n={decision_sample_n})" if len(records) == decision_sample_n else "")
                )
            except Exception as exc:
                console.print(
                    f"  [yellow]Could not load decision records: {exc}[/yellow]"
                )

        # --- Compliance reports ---
        report_standards = standard_list or []
        if not report_standards:
            try:
                from mrm.compliance.registry import compliance_registry

                compliance_registry.load_builtin_standards()
                report_standards = list(compliance_registry.list_standards())
            except Exception:
                pass

        # Run tests once per model (not per standard)
        test_results: Dict[str, Any] = {}
        model_configs = project.select_models(models=model_name)
        if model_configs and report_standards:
            mc = model_configs[0]
            try:
                runner = TestRunner(
                    project.config, project.backend, project.catalog
                )
                results = runner.run_tests([mc])
                model_results = results.get(model_name, {})
                test_results = model_results.get("test_results", {})
            except Exception as exc:
                console.print(
                    f"  [yellow]Could not run tests: {exc}[/yellow]"
                )

        for std_name in report_standards:
            try:
                from mrm.compliance.report_generator import (
                    generate_compliance_report,
                )

                if model_configs:
                    report_text = generate_compliance_report(
                        standard_name=std_name,
                        model_name=model_name,
                        model_config=model_configs[0],
                        test_results=test_results,
                        trigger_events=[],
                    )
                    model_data["reports"][std_name] = report_text
                    console.print(f"  Generated {std_name} report")
            except Exception as exc:
                console.print(
                    f"  [yellow]Could not generate {std_name} report: {exc}[/yellow]"
                )

        summary["models"][model_name] = model_data

    # --- Merkle roots ---
    merkle_roots: list = []
    try:
        roots_dir = PathLib(project.root_path / "evidence" / "roots")
        if roots_dir.exists():
            from mrm.evidence.merkle import read_root

            for root_file in sorted(roots_dir.glob("*.root.json")):
                epoch = root_file.stem.replace(".root", "")
                if start <= epoch <= end:
                    try:
                        root = read_root(roots_dir, epoch)
                        merkle_roots.append(root.to_dict())
                    except Exception:
                        pass
        if merkle_roots:
            console.print(f"  {len(merkle_roots)} Merkle root(s)")
    except Exception as exc:
        console.print(
            f"  [yellow]Could not load Merkle roots: {exc}[/yellow]"
        )
    summary["merkle_roots"] = merkle_roots

    # --- Inclusion proofs ---
    inclusion_proofs: list = []
    try:
        from mrm.portal.merkle_proof import build_inclusion_proof as _bip

        for epoch, hashes in all_leaf_hashes.items():
            valid_hashes = [h for h in hashes if h and len(h) == 64]
            for idx, _h in enumerate(valid_hashes):
                try:
                    proof = _bip(valid_hashes, idx, f"pkt-{idx}", epoch)
                    inclusion_proofs.append(proof.to_dict())
                except Exception:
                    pass
    except Exception as exc:
        console.print(
            f"  [yellow]Could not build inclusion proofs: {exc}[/yellow]"
        )
    if inclusion_proofs:
        console.print(f"  {len(inclusion_proofs)} inclusion proof(s)")
    summary["inclusion_proofs"] = inclusion_proofs

    return summary


@portal_app.command("export")
def portal_export(
    models: str = typer.Option(
        ..., "--models", "-m",
        help="Comma-separated model names to include",
    ),
    start: str = typer.Option(
        ..., "--start", "-s",
        help="Date range start (YYYY-MM-DD)",
    ),
    end: str = typer.Option(
        ..., "--end", "-e",
        help="Date range end (YYYY-MM-DD)",
    ),
    compliance: Optional[str] = typer.Option(
        None, "--compliance",
        help="Filter to a single compliance standard (e.g. standard:cps230)",
    ),
    output: str = typer.Option(
        "export.zip", "--output", "-o",
        help="Output ZIP file path",
    ),
    created_by: Optional[str] = typer.Option(
        None, "--created-by",
        help="User identifier (email)",
    ),
    include_decisions: bool = typer.Option(
        True, "--decisions/--no-decisions",
        help="Include decision records",
    ),
    decision_sample: int = typer.Option(
        500, "--decision-sample", "-n",
        help="Max decision records per model (statistical sample)",
    ),
    signer_name: Optional[str] = typer.Option(
        None, "--signer",
        help="Sign attestations: local | gpg | age | kms",
    ),
    key_path: Optional[str] = typer.Option(
        None, "--key-path",
        help="Key file for local signer",
    ),
    upload: Optional[str] = typer.Option(
        None, "--upload",
        help="Upload to S3 and generate pre-signed URL (s3://bucket/prefix)",
    ),
    presign_expiry: str = typer.Option(
        "7d", "--presign-expiry",
        help="Pre-signed URL expiry (e.g. 7d, 24h, 3600s)",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run",
        help="Preview what would be included without building the ZIP",
    ),
    evidence_backend: Optional[str] = typer.Option(
        None, "--evidence-backend",
        help="Override evidence backend type (local | s3)",
    ),
    evidence_bucket: Optional[str] = typer.Option(
        None, "--evidence-bucket",
        help="Override evidence S3 bucket",
    ),
    replay_backend: Optional[str] = typer.Option(
        None, "--replay-backend",
        help="Override replay backend type (local | s3)",
    ),
    replay_bucket: Optional[str] = typer.Option(
        None, "--replay-bucket",
        help="Override replay S3 bucket",
    ),
    profile: str = typer.Option(
        "dev", "--profile", "-p",
        help="Profile to use",
    ),
):
    """Build a self-verifying evidence export ZIP for regulators.

    The export contains evidence packets, decision records, compliance
    reports, Merkle roots, inclusion proofs, signed attestations, and a
    zero-dependency verify_export.py script the regulator runs to verify
    everything with ``python verify_export.py``.

    Examples:

        mrm portal export --models ccr_monte_carlo --start 2025-07-01 --end 2026-06-18

        mrm portal export -m ccr_monte_carlo -s 2025-07-01 -e 2026-06-18 --compliance standard:cps230 --signer local --key-path evidence/signer.key

        mrm portal export -m ccr_monte_carlo -s 2025-07-01 -e 2026-06-18 --upload s3://my-bucket/exports/ --presign-expiry 7d

        mrm portal export -m ccr_monte_carlo -s 2025-07-01 -e 2026-06-18 --dry-run

        mrm portal export -m ccr_monte_carlo -s 2025-07-01 -e 2026-06-18 --compliance standard:cps230 --decision-sample 100
    """
    import getpass
    import os
    from pathlib import Path as PathLib

    from mrm.portal.export import ExportBuilder, ExportScope

    try:
        project = Project.load(profile=profile)

        model_list = [m.strip() for m in models.split(",")]

        # Parse compliance standard (follows mrm docs generate pattern)
        standard_list = None
        if compliance:
            if ":" in compliance:
                prefix, standard_name = compliance.split(":", 1)
                if prefix != "standard":
                    console.print(
                        f"[red]Invalid compliance format '{compliance}'.[/red]"
                        " Use standard:<name> (e.g. standard:cps230)",
                        style="red",
                    )
                    raise typer.Exit(1)
            else:
                standard_name = compliance
            standard_list = [standard_name]

        # --- Collect all data ---
        data = _collect_export_data(
            project=project,
            model_list=model_list,
            standard_list=standard_list,
            start=start,
            end=end,
            include_decisions=include_decisions,
            decision_sample_n=decision_sample,
            evidence_backend=evidence_backend,
            evidence_bucket=evidence_bucket,
            replay_backend=replay_backend,
            replay_bucket=replay_bucket,
            profile=profile,
        )

        # --- Dry-run: just print summary ---
        if dry_run:
            console.print("\n[bold cyan]--- DRY RUN (no ZIP created) ---[/bold cyan]\n")
            table = Table(title="Export Preview", show_header=True)
            table.add_column("Model", style="cyan")
            table.add_column("Evidence Packets", justify="right")
            table.add_column("Decision Records", justify="right")
            table.add_column("Compliance Reports")
            for mname, mdata in data["models"].items():
                table.add_row(
                    mname,
                    str(len(mdata["packets"])),
                    str(len(mdata["decision_records"])),
                    ", ".join(mdata["reports"].keys()) or "(none)",
                )
            console.print(table)
            console.print(f"\nMerkle roots:     {len(data['merkle_roots'])}")
            console.print(f"Inclusion proofs: {len(data['inclusion_proofs'])}")
            console.print(f"Date range:       {start} to {end}")
            console.print(f"Decision sample:  n={decision_sample} per model")
            if compliance:
                console.print(f"Compliance:       {standard_list[0] if standard_list else compliance}")
            if signer_name:
                console.print(f"Signer:           {signer_name}")
            else:
                console.print("[yellow]Signer:           (none — attestations will be unsigned)[/yellow]")
            if upload:
                console.print(f"Upload target:    {upload}")
                console.print(f"Pre-sign expiry:  {presign_expiry}")
            console.print("\n[dim]Remove --dry-run to build the export.[/dim]")
            return

        # --- Build signer ---
        signer_obj = None
        if signer_name:
            signer_obj = _resolve_signer(signer_name, key_path)
            console.print(f"Signing attestations with: [bold]{signer_name}[/bold]")

        # --- Scope + builder ---
        scope = ExportScope(
            models=model_list,
            date_start=start,
            date_end=end,
            standards=standard_list,
            include_decision_records=include_decisions,
        )

        if not created_by:
            created_by = os.environ.get("USER", getpass.getuser())

        builder = ExportBuilder(
            scope=scope, created_by=created_by, signer=signer_obj
        )

        # Populate builder from collected data.
        for mname, mdata in data["models"].items():
            builder.add_evidence_packets(mname, mdata["packets"])
            if mdata["decision_records"]:
                builder.add_decision_records(mname, mdata["decision_records"])
            for std_name, report_text in mdata["reports"].items():
                builder.add_compliance_report(mname, std_name, report_text)

        builder.add_merkle_roots(data["merkle_roots"])
        builder.add_inclusion_proofs(data["inclusion_proofs"])

        # --- Build ZIP ---
        output_path = PathLib(output)
        builder.build(output_path)

        console.print(f"\n[green]Export created: {output_path}[/green]")
        console.print(f"  Export ID: {builder.export_id}")
        console.print(f"  Models:    {', '.join(model_list)}")
        console.print(f"  Range:     {start} to {end}")
        console.print(f"  Size:      {output_path.stat().st_size:,} bytes")
        if signer_obj:
            console.print(f"  Signed by: {signer_name}")

        # --- Upload to S3 with pre-signed URL ---
        if upload:
            _upload_export_to_s3(
                output_path, upload, presign_expiry, builder.export_id
            )
        else:
            console.print(
                f"\nDeliver [bold]{output_path.name}[/bold] to the regulator. "
                f"They run:"
            )
            console.print(f"  unzip {output_path.name}")
            console.print(f"  cd riskattest-export-*")
            console.print(f"  python verify_export.py")

    except typer.Exit:
        raise
    except FileNotFoundError as e:
        console.print(f" {e}", style="red")
        raise typer.Exit(1)
    except Exception as e:
        console.print(f" Error building export: {e}", style="red")
        import traceback

        traceback.print_exc()
        raise typer.Exit(1)


def _parse_expiry(expiry_str: str) -> int:
    """Parse a human-friendly expiry like '7d', '24h', '3600s' to seconds."""
    s = expiry_str.strip().lower()
    if s.endswith("d"):
        return int(s[:-1]) * 86400
    if s.endswith("h"):
        return int(s[:-1]) * 3600
    if s.endswith("s"):
        return int(s[:-1])
    return int(s)


def _upload_export_to_s3(
    zip_path: Path,
    s3_uri: str,
    presign_expiry: str,
    export_id: str,
) -> None:
    """Upload a ZIP to S3 and print a pre-signed download URL."""
    try:
        import boto3
    except ImportError:
        console.print(
            "S3 upload requires boto3: pip install boto3", style="red"
        )
        raise typer.Exit(1)

    # Parse s3://bucket/prefix
    if not s3_uri.startswith("s3://"):
        console.print(f"Invalid S3 URI: {s3_uri}", style="red")
        raise typer.Exit(1)

    parts = s3_uri[5:].split("/", 1)
    bucket = parts[0]
    prefix = parts[1] if len(parts) > 1 else ""
    if prefix and not prefix.endswith("/"):
        prefix += "/"

    key = f"{prefix}riskattest-export-{export_id}.zip"

    console.print(f"\nUploading to s3://{bucket}/{key} ...")
    s3 = boto3.client("s3")
    s3.upload_file(str(zip_path), bucket, key)
    console.print(f"[green]Uploaded.[/green]")

    # Generate pre-signed URL.
    expiry_seconds = _parse_expiry(presign_expiry)
    max_s3 = 604800  # 7 days
    if expiry_seconds > max_s3:
        console.print(
            f"[yellow]S3 pre-signed URLs max out at 7 days; "
            f"clamping {presign_expiry} to 7d.[/yellow]"
        )
        expiry_seconds = max_s3

    url = s3.generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket, "Key": key},
        ExpiresIn=expiry_seconds,
    )
    console.print(f"\n[bold]Pre-signed download URL[/bold] (expires in {presign_expiry}):")
    console.print(f"  {url}")
    console.print(
        f"\nSend this URL to the regulator. They download, unzip, "
        f"and run [bold]python verify_export.py[/bold]."
    )


@portal_app.command("list-exports")
def portal_list_exports(
    directory: str = typer.Option(
        ".", "--dir", "-d",
        help="Directory to scan for export ZIPs",
    ),
):
    """List RiskAttest export packages in a directory.

    Scans for ZIP files containing a ``manifest.json`` with an
    ``export_id`` field.
    """
    import json as _json
    from pathlib import Path as PathLib

    scan_dir = PathLib(directory)
    if not scan_dir.is_dir():
        console.print(f"Not a directory: {scan_dir}", style="red")
        raise typer.Exit(1)

    exports: list = []
    for zp in sorted(scan_dir.glob("*.zip")):
        try:
            with zipfile.ZipFile(zp, "r") as zf:
                for name in zf.namelist():
                    if name.endswith("manifest.json"):
                        data = _json.loads(zf.read(name))
                        if "export_id" in data:
                            exports.append(
                                {
                                    "file": zp.name,
                                    "export_id": data["export_id"],
                                    "created_at": data.get(
                                        "created_at", ""
                                    )[:19],
                                    "created_by": data.get(
                                        "created_by", ""
                                    ),
                                    "models": data.get("scope", {}).get(
                                        "models", []
                                    ),
                                    "packets": data.get("summary", {}).get(
                                        "total_evidence_packets", 0
                                    ),
                                }
                            )
                        break
        except Exception:
            pass

    if not exports:
        console.print("No RiskAttest exports found", style="yellow")
        raise typer.Exit(0)

    table = Table(title="RiskAttest Exports", show_header=True)
    table.add_column("File", style="cyan")
    table.add_column("Export ID")
    table.add_column("Created")
    table.add_column("By")
    table.add_column("Models")
    table.add_column("Packets", justify="right")

    for ex in exports:
        table.add_row(
            ex["file"],
            ex["export_id"][:12] + "...",
            ex["created_at"],
            ex["created_by"][:20],
            ", ".join(ex["models"])[:30],
            str(ex["packets"]),
        )

    console.print(table)


@portal_app.command("verify")
def portal_verify(
    zip_path: str = typer.Argument(
        ..., help="Path to export ZIP file"
    ),
):
    """Verify a RiskAttest export package.

    Extracts the ZIP to a temp directory, runs verify_export.py, and
    reports the result. This is exactly what the regulator does.

    Example:

        mrm portal verify /tmp/export.zip
    """
    import subprocess
    import tempfile

    from pathlib import Path as PathLib

    zp = PathLib(zip_path)
    if not zp.exists():
        console.print(f"File not found: {zp}", style="red")
        raise typer.Exit(1)

    if not zipfile.is_zipfile(zp):
        console.print(f"Not a valid ZIP: {zp}", style="red")
        raise typer.Exit(1)

    with tempfile.TemporaryDirectory() as tmpdir:
        # Extract
        with zipfile.ZipFile(zp, "r") as zf:
            zf.extractall(tmpdir)

        # Find export directory
        extracted = PathLib(tmpdir)
        dirs = [d for d in extracted.iterdir() if d.is_dir()]
        if not dirs:
            console.print("No export directory found in ZIP", style="red")
            raise typer.Exit(1)

        export_dir = dirs[0]

        verify_script = export_dir / "verify_export.py"
        if not verify_script.exists():
            console.print(
                "verify_export.py not found in export", style="red"
            )
            raise typer.Exit(1)

        console.print(f"Verifying: [bold]{zp.name}[/bold]")
        console.print(f"Export dir: {export_dir.name}\n")

        result = subprocess.run(
            [sys.executable, str(verify_script)],
            capture_output=True,
            text=True,
            cwd=str(export_dir),
        )

        # Print script output
        if result.stdout:
            console.print(result.stdout.rstrip())
        if result.stderr:
            console.print(result.stderr.rstrip(), style="dim")

        if result.returncode == 0:
            console.print(
                "\n[green]Export verification PASSED[/green]"
            )
        else:
            console.print(
                "\n[red]Export verification FAILED[/red]"
            )
            raise typer.Exit(1)


# ---------------------------------------------------------------------------
# mrm monitor — continuous model monitoring
# ---------------------------------------------------------------------------

monitor_app = typer.Typer(help="Continuous model monitoring with drift-triggered re-validation")
app.add_typer(monitor_app, name="monitor")


@monitor_app.command("run")
def monitor_run(
    models: Optional[str] = typer.Option(
        None, "--models", "-m",
        help="Comma-separated model names to monitor",
    ),
    all_models: bool = typer.Option(
        False, "--all",
        help="Monitor all models with monitoring.enabled: true",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run",
        help="Check metrics and report drift, don't revalidate",
    ),
    profile: str = typer.Option(
        "dev", "--profile", "-p",
        help="Profile to use",
    ),
):
    """Run one monitoring cycle.

    Collects metrics from configured sources, evaluates thresholds,
    and triggers re-validation if drift is detected. Designed to be
    called by the bank's scheduler (Airflow, cron, Databricks Workflows).

    Exit codes:
      0 = no drift detected
      1 = drift detected
      2 = error

    Examples:

        mrm monitor run --models ccr_monte_carlo

        mrm monitor run --all

        mrm monitor run --models ccr_monte_carlo --dry-run
    """
    from mrm.monitor.runner import MonitorRunner

    try:
        project = Project.load(profile=profile)

        # Determine which models to monitor
        if all_models:
            model_configs = project.list_models()
        elif models:
            model_configs = project.select_models(models=models)
        else:
            console.print(
                "Specify --models or --all", style="red"
            )
            raise typer.Exit(2)

        if not model_configs:
            console.print("No models selected", style="yellow")
            raise typer.Exit(0)

        log_dir = project.root_path / "evidence" / "monitoring"
        runner = MonitorRunner(log_dir=log_dir)

        overall_exit = 0
        results = []

        for mc in model_configs:
            model_name = mc.get("model", {}).get("name", "?")
            monitoring_raw = mc.get("monitoring")

            if not monitoring_raw or not monitoring_raw.get("enabled"):
                console.print(
                    f"  [dim]{model_name}: monitoring disabled, skipping[/dim]"
                )
                continue

            console.print(f"\nMonitoring [bold]{model_name}[/bold]...")

            if dry_run:
                # Dry run: collect metrics and report, don't revalidate
                from mrm.monitor.config import parse_monitoring_config
                from mrm.monitor.metrics import get_metric_source

                config = parse_monitoring_config(monitoring_raw)
                console.print(f"  Metrics configured: {len(config.metrics)}")
                for metric_cfg in config.metrics:
                    try:
                        source = get_metric_source(metric_cfg.source)
                        result = source.collect(metric_cfg)
                        status = "[red]DRIFT[/red]" if result.drifted else "[green]OK[/green]"
                        console.print(
                            f"  {metric_cfg.name}: {status} "
                            f"(value={result.value:.4f}, threshold={metric_cfg.threshold})"
                        )
                    except Exception as exc:
                        console.print(
                            f"  {metric_cfg.name}: [yellow]ERROR: {exc}[/yellow]"
                        )
                continue

            result = runner.run(mc)
            results.append(result)

            if result.skipped:
                console.print(f"  [dim]Skipped (disabled)[/dim]")
            elif result.exit_code == 2:
                console.print(f"  [red]Error: {result.error}[/red]")
                overall_exit = max(overall_exit, 2)
            elif result.overall_drifted:
                console.print(f"  [red]Drift detected[/red]")
                for m in result.metric_results:
                    status = "[red]DRIFT[/red]" if m.drifted else "[green]OK[/green]"
                    console.print(
                        f"    {m.name}: {status} "
                        f"(value={m.value:.4f}, threshold={m.threshold})"
                    )
                overall_exit = max(overall_exit, 1)
            else:
                console.print(f"  [green]No drift[/green]")
                for m in result.metric_results:
                    console.print(
                        f"    {m.name}: [green]OK[/green] "
                        f"(value={m.value:.4f}, threshold={m.threshold})"
                    )

        if dry_run:
            console.print("\n[dim]Dry run complete. Remove --dry-run to trigger actions.[/dim]")
            raise typer.Exit(0)

        # Print summary
        console.print()
        drifted_count = sum(1 for r in results if r.overall_drifted)
        error_count = sum(1 for r in results if r.exit_code == 2)
        ok_count = sum(
            1 for r in results
            if not r.overall_drifted and r.exit_code != 2 and not r.skipped
        )
        console.print(
            f"Summary: {ok_count} OK, {drifted_count} drifted, "
            f"{error_count} error(s)"
        )

        raise typer.Exit(overall_exit)

    except typer.Exit:
        raise
    except FileNotFoundError as e:
        console.print(f" {e}", style="red")
        raise typer.Exit(2)
    except Exception as e:
        console.print(f" Error: {e}", style="red")
        import traceback
        traceback.print_exc()
        raise typer.Exit(2)


@monitor_app.command("history")
def monitor_history(
    model: str = typer.Option(
        ..., "--model", "-m",
        help="Model name",
    ),
    last: int = typer.Option(
        10, "--last", "-n",
        help="Number of entries to show",
    ),
    profile: str = typer.Option(
        "dev", "--profile", "-p",
        help="Profile to use",
    ),
):
    """Show monitoring history for a model.

    Example:

        mrm monitor history --model ccr_monte_carlo --last 10
    """
    from mrm.monitor.log import MonitoringLog

    try:
        project = Project.load(profile=profile)
        log_dir = project.root_path / "evidence" / "monitoring" / model
        log = MonitoringLog(log_dir)
        entries = log.read_last(last)

        if not entries:
            console.print(f"No monitoring history for {model}", style="yellow")
            raise typer.Exit(0)

        table = Table(
            title=f"Monitoring History: {model} (last {last})",
            show_header=True,
        )
        table.add_column("Timestamp", style="cyan")
        table.add_column("Run ID", style="dim", max_width=12)
        table.add_column("Drifted", justify="center")
        table.add_column("Action")
        table.add_column("Exit")

        for entry in entries:
            ts = entry.get("timestamp", "?")[:19]
            run_id = entry.get("run_id", "?")[:8]
            drifted = entry.get("overall_drifted", False)
            drifted_str = "[red]YES[/red]" if drifted else "[green]no[/green]"
            action = entry.get("action_taken", "none")
            exit_code = str(entry.get("exit_code", "?"))

            table.add_row(ts, run_id, drifted_str, action, exit_code)

        console.print(table)

    except typer.Exit:
        raise
    except FileNotFoundError as e:
        console.print(f" {e}", style="red")
        raise typer.Exit(1)
    except Exception as e:
        console.print(f" Error: {e}", style="red")
        raise typer.Exit(1)


@monitor_app.command("status")
def monitor_status(
    profile: str = typer.Option(
        "dev", "--profile", "-p",
        help="Profile to use",
    ),
):
    """Show monitoring status across all models.

    Example:

        mrm monitor status
    """
    from mrm.monitor.log import MonitoringLog

    try:
        project = Project.load(profile=profile)
        all_models = project.list_models()

        table = Table(
            title="Monitoring Status",
            show_header=True,
        )
        table.add_column("Model", style="cyan")
        table.add_column("Monitoring", justify="center")
        table.add_column("Last Run", style="dim")
        table.add_column("Last Status", justify="center")
        table.add_column("Total Runs", justify="right")

        for mc in all_models:
            model_name = mc.get("model", {}).get("name", "?")
            monitoring_raw = mc.get("monitoring", {})
            enabled = monitoring_raw.get("enabled", False)

            enabled_str = "[green]on[/green]" if enabled else "[dim]off[/dim]"
            last_run = "-"
            last_status = "-"
            total_runs = "0"

            if enabled:
                log_dir = (
                    project.root_path / "evidence" / "monitoring" / model_name
                )
                log = MonitoringLog(log_dir)
                entries = log.read_all()
                total_runs = str(len(entries))
                if entries:
                    last_entry = entries[-1]
                    last_run = last_entry.get("timestamp", "?")[:16]
                    drifted = last_entry.get("overall_drifted", False)
                    last_status = (
                        "[red]DRIFT[/red]" if drifted else "[green]OK[/green]"
                    )

            table.add_row(
                model_name, enabled_str, last_run, last_status, total_runs
            )

        console.print(table)

    except typer.Exit:
        raise
    except FileNotFoundError as e:
        console.print(f" {e}", style="red")
        raise typer.Exit(1)
    except Exception as e:
        console.print(f" Error: {e}", style="red")
        raise typer.Exit(1)


if __name__ == "__main__":
    app()

"""Technology stacks the agent can build with: one sensible framework per language.

Each stack defines the toolchain it needs (with install help), the commands used to set up, build, test and lint,
a small scaffold that already builds and passes its test, and how the user can run or package the result.
Custom stacks can be added in a project's agent/config.json by overriding "commands" and "stack".
"""
from __future__ import annotations

import re
import shutil
import subprocess

# Install help per tool: Debian/Ubuntu/Pop!_OS command and an official page for everything else.
TOOLS = {
    "python3": {"name": "Python 3 with venv/pip", "cmd": "sudo apt install python3 python3-venv python3-pip", "url": "https://www.python.org/downloads/"},
    "node": {"name": "Node.js 20+", "cmd": "sudo apt install nodejs npm   # or use nvm/fnm for a current version", "url": "https://nodejs.org/en/download"},
    "npm": {"name": "npm", "cmd": "sudo apt install npm", "url": "https://nodejs.org/en/download"},
    "dotnet": {"name": ".NET SDK 8+", "cmd": "sudo apt install dotnet-sdk-8.0", "url": "https://learn.microsoft.com/dotnet/core/install/linux"},
    "go": {"name": "Go 1.21+", "cmd": "sudo apt install golang-go   # or the tarball from go.dev/dl", "url": "https://go.dev/doc/install"},
    "cargo": {"name": "Rust toolchain (cargo)", "cmd": "sudo apt install build-essential && curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh", "url": "https://rustup.rs"},
    "java": {"name": "JDK 17+", "cmd": "sudo apt install default-jdk", "url": "https://adoptium.net/"},
    "mvn": {"name": "Apache Maven", "cmd": "sudo apt install maven", "url": "https://maven.apache.org/install.html"},
    "php": {"name": "PHP 8.1+ CLI", "cmd": "sudo apt install php-cli php-xml php-mbstring php-curl unzip", "url": "https://www.php.net/downloads"},
    "composer": {"name": "Composer", "cmd": "sudo apt install composer", "url": "https://getcomposer.org/download/"},
    "ruby": {"name": "Ruby 3+", "cmd": "sudo apt install ruby-full build-essential", "url": "https://www.ruby-lang.org/en/downloads/"},
    "bundle": {"name": "Bundler", "cmd": "sudo apt install ruby-bundler   # or: sudo gem install bundler", "url": "https://bundler.io/"},
}

STACKS: dict[str, dict] = {
    "fastapi": {
        "label": "Python / FastAPI",
        "language": "Python",
        "stack": "Python 3, FastAPI, Jinja2 templates, SQLite via sqlite3/SQLAlchemy, pytest",
        "tools": ["python3"],
        "commands": {
            "setup": "python3 -m venv .venv && .venv/bin/pip install -q -r requirements.txt",
            "build": ".venv/bin/python -m compileall -q app tests && .venv/bin/python -c 'import app.main'",
            "test": ".venv/bin/python -m pytest -q -x",
            "lint": "",
        },
        "deps_files": ["requirements.txt"],
        "source_exts": [".py", ".html", ".css", ".js", ".sql", ".toml", ".yml", ".yaml"],
        "src_dirs": ["app"],
        "gitignore": ".venv/\n__pycache__/\n.pytest_cache/\n",
        "run": ".venv/bin/uvicorn app.main:app --reload",
        "url": "http://127.0.0.1:8000 (API docs at /docs)",
        "release": [],
        "artifact": "",
        "scaffold": {
            "requirements.txt": "fastapi\nuvicorn\njinja2\nhttpx\npytest\npython-multipart\n",
            "app/__init__.py": "",
            "app/main.py": ('"""Application entry point."""\nfrom fastapi import FastAPI\n\napp = FastAPI(title="App")\n\n\n'
                            '@app.get("/health")\ndef health() -> dict:\n    """Liveness check."""\n    return {"status": "ok"}\n'),
            "tests/__init__.py": "",
            "tests/test_health.py": ('from fastapi.testclient import TestClient\n\nfrom app.main import app\n\n\n'
                                     'def test_health():\n    assert TestClient(app).get("/health").json() == {"status": "ok"}\n'),
        },
    },
    "javascript": {
        "label": "JavaScript / Node.js + Express",
        "language": "JavaScript",
        "stack": "Node.js (CommonJS), Express, EJS or plain HTML templates, SQLite via better-sqlite3, node:test",
        "tools": ["node", "npm"],
        "commands": {
            "setup": "npm install --no-audit --no-fund",
            "build": "node -e \"require('./src/app')\"",
            "test": "node --test",
            "lint": "",
        },
        "deps_files": ["package.json"],
        "source_exts": [".js", ".mjs", ".cjs", ".json", ".html", ".css", ".ejs", ".sql", ".yml", ".yaml"],
        "src_dirs": ["src"],
        "gitignore": "node_modules/\n",
        "run": "npm start",
        "url": "http://127.0.0.1:3000",
        "release": [],
        "artifact": "",
        "scaffold": {
            "package.json": ('{\n  "name": "app",\n  "version": "0.1.0",\n  "private": true,\n  "scripts": {\n'
                             '    "start": "node src/server.js",\n    "test": "node --test"\n  },\n'
                             '  "dependencies": {\n    "express": "^4.19.2"\n  }\n}\n'),
            "src/app.js": ("'use strict';\nconst express = require('express');\n\nconst app = express();\n\n"
                           "app.get('/health', (req, res) => res.json({ status: 'ok' }));\n\nmodule.exports = app;\n"),
            "src/server.js": ("'use strict';\nconst app = require('./app');\n\nconst port = process.env.PORT || 3000;\n"
                              "app.listen(port, () => console.log(`listening on ${port}`));\n"),
            "tests/health.test.js": ("'use strict';\nconst test = require('node:test');\nconst assert = require('node:assert');\n"
                                     "const app = require('../src/app');\n\n"
                                     "test('health endpoint', async () => {\n  const server = app.listen(0);\n"
                                     "  try {\n    const res = await fetch(`http://127.0.0.1:${server.address().port}/health`);\n"
                                     "    assert.deepStrictEqual(await res.json(), { status: 'ok' });\n"
                                     "  } finally {\n    server.close();\n  }\n});\n"),
        },
    },
    "typescript": {
        "label": "TypeScript / Node.js + Express",
        "language": "TypeScript",
        "stack": "TypeScript (strict), Node.js, Express, SQLite via better-sqlite3, vitest + supertest",
        "tools": ["node", "npm"],
        "commands": {
            "setup": "npm install --no-audit --no-fund",
            "build": "npx tsc",
            "test": "npx vitest run",
            "lint": "",
        },
        "deps_files": ["package.json"],
        "source_exts": [".ts", ".tsx", ".js", ".json", ".html", ".css", ".sql", ".yml", ".yaml"],
        "src_dirs": ["src"],
        "gitignore": "node_modules/\ndist/\n",
        "run": "npm run dev",
        "url": "http://127.0.0.1:3000",
        "release": [["npm", "run", "build"]],
        "artifact": "dist/ (run with: node dist/server.js)",
        "scaffold": {
            "package.json": ('{\n  "name": "app",\n  "version": "0.1.0",\n  "private": true,\n  "scripts": {\n'
                             '    "dev": "tsx watch src/server.ts",\n    "build": "tsc",\n    "start": "node dist/server.js",\n'
                             '    "test": "vitest run"\n  },\n'
                             '  "dependencies": {\n    "express": "^4.19.2"\n  },\n'
                             '  "devDependencies": {\n    "@types/express": "^4.17.21",\n    "@types/node": "^20.14.0",\n'
                             '    "@types/supertest": "^6.0.2",\n    "supertest": "^7.0.0",\n    "tsx": "^4.16.0",\n'
                             '    "typescript": "^5.5.0",\n    "vitest": "^2.0.0"\n  }\n}\n'),
            "tsconfig.json": ('{\n  "compilerOptions": {\n    "target": "ES2022",\n    "module": "commonjs",\n    "outDir": "dist",\n'
                              '    "rootDir": "src",\n    "strict": true,\n    "esModuleInterop": true,\n    "skipLibCheck": true\n  },\n'
                              '  "include": ["src"]\n}\n'),
            "src/app.ts": ("import express from 'express';\n\nexport const app = express();\n\n"
                           "app.get('/health', (_req, res) => {\n  res.json({ status: 'ok' });\n});\n"),
            "src/server.ts": ("import { app } from './app';\n\nconst port = Number(process.env.PORT) || 3000;\n"
                              "app.listen(port, () => console.log(`listening on ${port}`));\n"),
            "tests/health.test.ts": ("import request from 'supertest';\nimport { expect, test } from 'vitest';\n"
                                     "import { app } from '../src/app';\n\n"
                                     "test('health endpoint', async () => {\n  const res = await request(app).get('/health');\n"
                                     "  expect(res.body).toEqual({ status: 'ok' });\n});\n"),
        },
    },
    "go": {
        "label": "Go / net/http",
        "language": "Go",
        "stack": "Go 1.21+, standard library net/http and html/template, SQLite via modernc.org/sqlite, testing + httptest",
        "tools": ["go"],
        "commands": {
            "setup": "go mod tidy",
            "build": "go build ./...",
            "test": "go test ./...",
            "lint": "go vet ./...",
        },
        "deps_files": ["go.mod"],
        "source_exts": [".go", ".html", ".css", ".js", ".sql", ".yml", ".yaml", ".mod"],
        "src_dirs": ["."],
        "gitignore": "dist/\n",
        "run": "go run .",
        "url": "http://127.0.0.1:8080",
        "release": [["go", "build", "-o", "dist/app", "."]],
        "artifact": "dist/app",
        "scaffold": {
            "go.mod": "module app\n\ngo 1.21\n",
            "main.go": ('package main\n\nimport (\n\t"encoding/json"\n\t"log"\n\t"net/http"\n\t"os"\n)\n\n'
                        '// newMux builds the HTTP routes; kept separate from main so tests can use it.\n'
                        'func newMux() *http.ServeMux {\n\tmux := http.NewServeMux()\n'
                        '\tmux.HandleFunc("/health", func(w http.ResponseWriter, r *http.Request) {\n'
                        '\t\tw.Header().Set("Content-Type", "application/json")\n'
                        '\t\tjson.NewEncoder(w).Encode(map[string]string{"status": "ok"})\n\t})\n\treturn mux\n}\n\n'
                        'func main() {\n\taddr := ":8080"\n\tif p := os.Getenv("PORT"); p != "" {\n\t\taddr = ":" + p\n\t}\n'
                        '\tlog.Printf("listening on %s", addr)\n\tlog.Fatal(http.ListenAndServe(addr, newMux()))\n}\n'),
            "main_test.go": ('package main\n\nimport (\n\t"net/http"\n\t"net/http/httptest"\n\t"strings"\n\t"testing"\n)\n\n'
                             'func TestHealth(t *testing.T) {\n\trec := httptest.NewRecorder()\n'
                             '\tnewMux().ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/health", nil))\n'
                             '\tif rec.Code != http.StatusOK || !strings.Contains(rec.Body.String(), `"status":"ok"`) {\n'
                             '\t\tt.Fatalf("unexpected response: %d %s", rec.Code, rec.Body.String())\n\t}\n}\n'),
        },
    },
    "rust": {
        "label": "Rust / Axum",
        "language": "Rust",
        "stack": "Rust, Axum on Tokio, askama or plain HTML templates, SQLite via rusqlite, cargo test",
        "tools": ["cargo"],
        "commands": {
            "setup": "cargo fetch",
            "build": "cargo build",
            "test": "cargo test",
            "lint": "",
        },
        "deps_files": ["Cargo.toml"],
        "source_exts": [".rs", ".toml", ".html", ".css", ".js", ".sql", ".yml", ".yaml"],
        "src_dirs": ["src"],
        "gitignore": "target/\n",
        "run": "cargo run --release",
        "url": "http://127.0.0.1:3000",
        "release": [["cargo", "build", "--release"]],
        "artifact": "target/release/app",
        "scaffold": {
            "Cargo.toml": ('[package]\nname = "app"\nversion = "0.1.0"\nedition = "2021"\n\n[dependencies]\n'
                           'axum = "0.7"\ntokio = { version = "1", features = ["full"] }\nserde_json = "1"\n\n'
                           '[dev-dependencies]\ntower = { version = "0.5", features = ["util"] }\nhttp-body-util = "0.1"\n'),
            "src/lib.rs": ('use axum::{routing::get, Json, Router};\nuse serde_json::{json, Value};\n\n'
                           '/// Builds the application router; kept in the library so tests can use it.\n'
                           'pub fn app() -> Router {\n    Router::new().route("/health", get(health))\n}\n\n'
                           'async fn health() -> Json<Value> {\n    Json(json!({ "status": "ok" }))\n}\n'),
            "src/main.rs": ('#[tokio::main]\nasync fn main() {\n    let port = std::env::var("PORT").unwrap_or_else(|_| "3000".into());\n'
                            '    let listener = tokio::net::TcpListener::bind(format!("0.0.0.0:{port}")).await.unwrap();\n'
                            '    println!("listening on {port}");\n    axum::serve(listener, app::app()).await.unwrap();\n}\n'),
            "tests/health.rs": ('use axum::{body::Body, http::Request};\nuse http_body_util::BodyExt;\nuse tower::ServiceExt;\n\n'
                                '#[tokio::test]\nasync fn health_returns_ok() {\n'
                                '    let res = app::app()\n        .oneshot(Request::get("/health").body(Body::empty()).unwrap())\n'
                                '        .await\n        .unwrap();\n    assert_eq!(res.status(), 200);\n'
                                '    let body = res.into_body().collect().await.unwrap().to_bytes();\n'
                                '    assert!(String::from_utf8_lossy(&body).contains("ok"));\n}\n'),
        },
    },
    "csharp": {
        "label": "C# / ASP.NET Core",
        "language": "C#",
        "stack": "C# on .NET, ASP.NET Core minimal APIs, Razor or plain HTML, SQLite via Microsoft.Data.Sqlite or EF Core, xUnit",
        "tools": ["dotnet"],
        "commands": {
            "setup": "[ -f App.sln ] || dotnet new sln -n App >/dev/null; dotnet sln App.sln add src/App/App.csproj tests/App.Tests/App.Tests.csproj >/dev/null; dotnet restore",
            "build": "dotnet build --no-restore -nologo -v q",
            "test": "dotnet test --no-build -nologo -v q",
            "lint": "",
        },
        "deps_files": ["src/App/App.csproj", "tests/App.Tests/App.Tests.csproj"],
        "source_exts": [".cs", ".csproj", ".cshtml", ".razor", ".json", ".html", ".css", ".js", ".sql", ".yml", ".yaml"],
        "src_dirs": ["src"],
        "gitignore": "bin/\nobj/\ndist/\n",
        "run": "dotnet run --project src/App",
        "url": "http://localhost:5000 (see the URL printed on start)",
        "release": [["dotnet", "publish", "src/App", "-c", "Release", "-o", "dist"]],
        "artifact": "dist/App (self-contained start: ./dist/App)",
        "scaffold": {
            "src/App/App.csproj": ('<Project Sdk="Microsoft.NET.Sdk.Web">\n  <PropertyGroup>\n    <TargetFramework>{dotnet_tfm}</TargetFramework>\n'
                                   '    <Nullable>enable</Nullable>\n    <ImplicitUsings>enable</ImplicitUsings>\n  </PropertyGroup>\n</Project>\n'),
            "src/App/Program.cs": ('var builder = WebApplication.CreateBuilder(args);\nvar app = builder.Build();\n\n'
                                   'app.MapGet("/health", () => Results.Json(new { status = "ok" }));\n\napp.Run();\n\n'
                                   '// Needed so the test project can start the app.\npublic partial class Program { }\n'),
            "tests/App.Tests/App.Tests.csproj": ('<Project Sdk="Microsoft.NET.Sdk">\n  <PropertyGroup>\n    <TargetFramework>{dotnet_tfm}</TargetFramework>\n'
                                                 '    <Nullable>enable</Nullable>\n    <ImplicitUsings>enable</ImplicitUsings>\n    <IsPackable>false</IsPackable>\n  </PropertyGroup>\n'
                                                 '  <ItemGroup>\n    <PackageReference Include="Microsoft.AspNetCore.Mvc.Testing" Version="{dotnet_pkg}" />\n'
                                                 '    <PackageReference Include="Microsoft.NET.Test.Sdk" Version="17.*" />\n'
                                                 '    <PackageReference Include="xunit" Version="2.*" />\n'
                                                 '    <PackageReference Include="xunit.runner.visualstudio" Version="2.*" />\n  </ItemGroup>\n'
                                                 '  <ItemGroup>\n    <ProjectReference Include="../../src/App/App.csproj" />\n  </ItemGroup>\n</Project>\n'),
            "tests/App.Tests/HealthTests.cs": ('using Microsoft.AspNetCore.Mvc.Testing;\nusing Xunit;\n\n'
                                               'public class HealthTests : IClassFixture<WebApplicationFactory<Program>>\n{\n'
                                               '    private readonly WebApplicationFactory<Program> _factory;\n\n'
                                               '    public HealthTests(WebApplicationFactory<Program> factory) => _factory = factory;\n\n'
                                               '    [Fact]\n    public async Task Health_returns_ok()\n    {\n'
                                               '        var res = await _factory.CreateClient().GetAsync("/health");\n'
                                               '        res.EnsureSuccessStatusCode();\n'
                                               '        Assert.Contains("ok", await res.Content.ReadAsStringAsync());\n    }\n}\n'),
        },
    },
    "java": {
        "label": "Java / Spring Boot",
        "language": "Java",
        "stack": "Java 17+, Spring Boot (Web, Thymeleaf, Data JPA with H2/SQLite), Maven, JUnit 5 + MockMvc",
        "tools": ["java", "mvn"],
        "commands": {
            "setup": "mvn -q -B dependency:go-offline",
            "build": "mvn -q -B -DskipTests package",
            "test": "mvn -q -B test",
            "lint": "",
        },
        "deps_files": ["pom.xml"],
        "source_exts": [".java", ".xml", ".html", ".properties", ".yml", ".yaml", ".sql", ".css", ".js"],
        "src_dirs": ["src/main"],
        "gitignore": "target/\n",
        "run": "mvn spring-boot:run",
        "url": "http://127.0.0.1:8080",
        "release": [["mvn", "-q", "-B", "-DskipTests", "package"]],
        "artifact": "target/*.jar (run with: java -jar target/app-0.1.0.jar)",
        "scaffold": {
            "pom.xml": ('<?xml version="1.0" encoding="UTF-8"?>\n<project xmlns="http://maven.apache.org/POM/4.0.0">\n'
                        '  <modelVersion>4.0.0</modelVersion>\n  <parent>\n    <groupId>org.springframework.boot</groupId>\n'
                        '    <artifactId>spring-boot-starter-parent</artifactId>\n    <version>3.3.5</version>\n  </parent>\n'
                        '  <groupId>com.example</groupId>\n  <artifactId>app</artifactId>\n  <version>0.1.0</version>\n'
                        '  <properties>\n    <java.version>17</java.version>\n  </properties>\n  <dependencies>\n'
                        '    <dependency>\n      <groupId>org.springframework.boot</groupId>\n      <artifactId>spring-boot-starter-web</artifactId>\n    </dependency>\n'
                        '    <dependency>\n      <groupId>org.springframework.boot</groupId>\n      <artifactId>spring-boot-starter-test</artifactId>\n      <scope>test</scope>\n    </dependency>\n'
                        '  </dependencies>\n  <build>\n    <plugins>\n      <plugin>\n        <groupId>org.springframework.boot</groupId>\n'
                        '        <artifactId>spring-boot-maven-plugin</artifactId>\n      </plugin>\n    </plugins>\n  </build>\n</project>\n'),
            "src/main/java/com/example/app/Application.java": ('package com.example.app;\n\nimport org.springframework.boot.SpringApplication;\n'
                                                               'import org.springframework.boot.autoconfigure.SpringBootApplication;\n\n'
                                                               '@SpringBootApplication\npublic class Application {\n'
                                                               '    public static void main(String[] args) {\n        SpringApplication.run(Application.class, args);\n    }\n}\n'),
            "src/main/java/com/example/app/HealthController.java": ('package com.example.app;\n\nimport java.util.Map;\n'
                                                                    'import org.springframework.web.bind.annotation.GetMapping;\n'
                                                                    'import org.springframework.web.bind.annotation.RestController;\n\n'
                                                                    '@RestController\npublic class HealthController {\n    @GetMapping("/health")\n'
                                                                    '    public Map<String, String> health() {\n        return Map.of("status", "ok");\n    }\n}\n'),
            "src/test/java/com/example/app/HealthControllerTest.java": ('package com.example.app;\n\nimport static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;\n'
                                                                        'import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;\n'
                                                                        'import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;\n\n'
                                                                        'import org.junit.jupiter.api.Test;\nimport org.springframework.beans.factory.annotation.Autowired;\n'
                                                                        'import org.springframework.boot.test.autoconfigure.web.servlet.WebMvcTest;\n'
                                                                        'import org.springframework.test.web.servlet.MockMvc;\n\n'
                                                                        '@WebMvcTest(HealthController.class)\nclass HealthControllerTest {\n'
                                                                        '    @Autowired\n    private MockMvc mvc;\n\n    @Test\n    void healthReturnsOk() throws Exception {\n'
                                                                        '        mvc.perform(get("/health")).andExpect(status().isOk()).andExpect(jsonPath("$.status").value("ok"));\n    }\n}\n'),
        },
    },
    "php": {
        "label": "PHP / Slim",
        "language": "PHP",
        "stack": "PHP 8.1+, Slim 4 with PSR-7, Twig templates, SQLite via PDO, PHPUnit",
        "tools": ["php", "composer"],
        "commands": {
            "setup": "composer install --no-interaction --quiet",
            "build": "find src public -name '*.php' -print0 | xargs -0 -n1 php -l >/dev/null",
            "test": "vendor/bin/phpunit --colors=never",
            "lint": "",
        },
        "deps_files": ["composer.json"],
        "source_exts": [".php", ".twig", ".json", ".html", ".css", ".js", ".sql", ".yml", ".yaml"],
        "src_dirs": ["src", "public"],
        "gitignore": "vendor/\n.phpunit.cache/\n",
        "run": "php -S 127.0.0.1:8080 -t public",
        "url": "http://127.0.0.1:8080",
        "release": [],
        "artifact": "",
        "scaffold": {
            "composer.json": ('{\n  "require": {\n    "slim/slim": "^4.12",\n    "slim/psr7": "^1.6"\n  },\n'
                              '  "require-dev": {\n    "phpunit/phpunit": "^10.5"\n  },\n'
                              '  "autoload": {\n    "psr-4": {\n      "App\\\\": "src/"\n    }\n  }\n}\n'),
            "phpunit.xml": ('<?xml version="1.0" encoding="UTF-8"?>\n<phpunit bootstrap="vendor/autoload.php" colors="false">\n'
                            '  <testsuites>\n    <testsuite name="app">\n      <directory>tests</directory>\n    </testsuite>\n  </testsuites>\n</phpunit>\n'),
            "src/AppFactory.php": ('<?php\n\ndeclare(strict_types=1);\n\nnamespace App;\n\nuse Slim\\App;\nuse Slim\\Factory\\AppFactory as SlimFactory;\n\n'
                                   'final class AppFactory\n{\n    /** Builds the Slim application; kept separate so tests can use it. */\n'
                                   '    public static function create(): App\n    {\n        $app = SlimFactory::create();\n'
                                   '        $app->get(\'/health\', function ($request, $response) {\n'
                                   '            $response->getBody()->write(json_encode([\'status\' => \'ok\']));\n'
                                   '            return $response->withHeader(\'Content-Type\', \'application/json\');\n        });\n'
                                   '        return $app;\n    }\n}\n'),
            "public/index.php": "<?php\n\ndeclare(strict_types=1);\n\nrequire __DIR__ . '/../vendor/autoload.php';\n\nApp\\AppFactory::create()->run();\n",
            "tests/HealthTest.php": ('<?php\n\ndeclare(strict_types=1);\n\nuse App\\AppFactory;\nuse PHPUnit\\Framework\\TestCase;\nuse Slim\\Psr7\\Factory\\ServerRequestFactory;\n\n'
                                     'final class HealthTest extends TestCase\n{\n    public function testHealthReturnsOk(): void\n    {\n'
                                     '        $response = AppFactory::create()->handle((new ServerRequestFactory())->createServerRequest(\'GET\', \'/health\'));\n'
                                     '        $this->assertSame(200, $response->getStatusCode());\n'
                                     '        $this->assertStringContainsString(\'ok\', (string) $response->getBody());\n    }\n}\n'),
        },
    },
    "ruby": {
        "label": "Ruby / Sinatra",
        "language": "Ruby",
        "stack": "Ruby 3+, Sinatra with ERB templates, SQLite via sqlite3 or Sequel, Minitest + rack-test",
        "tools": ["ruby", "bundle"],
        "commands": {
            "setup": "bundle install --quiet",
            "build": "bundle exec ruby -e \"require './app'\"",
            "test": "bundle exec ruby -Itest -e 'Dir[\"test/**/*_test.rb\"].sort.each { |f| require \"./#{f}\" }'",
            "lint": "",
        },
        "deps_files": ["Gemfile"],
        "source_exts": [".rb", ".erb", ".ru", ".html", ".css", ".js", ".sql", ".yml", ".yaml"],
        "src_dirs": ["lib", "app.rb"],
        "gitignore": ".bundle/\nvendor/bundle/\n",
        "run": "bundle exec rackup -p 4567",
        "url": "http://127.0.0.1:4567",
        "release": [],
        "artifact": "",
        "scaffold": {
            "Gemfile": ('source "https://rubygems.org"\n\ngem "sinatra", "~> 4.0"\ngem "rackup"\ngem "puma"\n\n'
                        'group :test do\n  gem "minitest"\n  gem "rack-test"\nend\n'),
            "app.rb": ('require "sinatra/base"\nrequire "json"\n\n# Application entry point.\nclass App < Sinatra::Base\n'
                       '  get "/health" do\n    content_type :json\n    { status: "ok" }.to_json\n  end\nend\n'),
            "config.ru": 'require_relative "app"\n\nrun App\n',
            "test/health_test.rb": ('ENV["APP_ENV"] = "test"\nrequire "minitest/autorun"\nrequire "rack/test"\nrequire_relative "../app"\n\n'
                                    'class HealthTest < Minitest::Test\n  include Rack::Test::Methods\n\n  def app\n    App\n  end\n\n'
                                    '  def test_health\n    get "/health"\n    assert last_response.ok?\n    assert_includes last_response.body, "ok"\n  end\nend\n'),
        },
    },
    # Selftest only: no dependencies. Not offered to users.
    "minimal": {
        "label": "Python standard library (selftest)",
        "language": "Python",
        "stack": "Python 3 standard library only, unittest",
        "tools": ["python3"],
        "commands": {
            "setup": "",
            "build": "python3 -m compileall -q app tests",
            "test": "python3 -m unittest discover -s tests -q",
            "lint": "",
        },
        "deps_files": [],
        "source_exts": [".py", ".html", ".css", ".js", ".sql", ".toml", ".yml", ".yaml"],
        "src_dirs": ["app"],
        "gitignore": "__pycache__/\n",
        "run": "python3 -m unittest discover -s tests",
        "url": "",
        "release": [],
        "artifact": "",
        "hidden": True,
        "scaffold": {
            "app/__init__.py": "",
            "app/main.py": '"""Entry point."""\n\n\ndef health() -> str:\n    return "ok"\n',
            "tests/__init__.py": "",
            "tests/test_health.py": ('import unittest\n\nfrom app.main import health\n\n\nclass T(unittest.TestCase):\n'
                                     '    def test_health(self):\n        self.assertEqual(health(), "ok")\n'),
        },
    },
}

COMMON_GITIGNORE = ".aider*\n.env\n*.db\n"


def public_stacks() -> list[str]:
    return [k for k, v in STACKS.items() if not v.get("hidden")]


def missing_tools(key: str) -> list[dict]:
    """Tools required by the stack that are not on PATH, each with install help."""
    if key not in STACKS:  # "auto" before the stack was chosen
        return []
    return [{"tool": t, **TOOLS[t]} for t in STACKS[key]["tools"] if shutil.which(t) is None]


def install_help(key: str) -> str:
    miss = missing_tools(key)
    if not miss:
        return ""
    lines = [f"Missing for {STACKS[key]['label']}:"]
    for m in miss:
        lines += [f"  - {m['name']} (command: {m['tool']})", f"      install:  {m['cmd']}", f"      more:     {m['url']}"]
    lines.append("Install it, open a new terminal if the command is still not found, then retry.")
    return "\n".join(lines)


def availability() -> list[dict]:
    out = []
    for k in public_stacks():
        s = STACKS[k]
        out.append({"key": k, "label": s["label"], "language": s["language"], "available": not missing_tools(k),
                    "missing": missing_tools(k),
                    "help": install_help(k) if missing_tools(k) else ""})
    return out


def _dotnet_versions() -> tuple[str, str]:
    """Target framework moniker and matching package version for the installed .NET SDK (default 8)."""
    try:
        major = int(re.match(r"(\d+)", subprocess.run(["dotnet", "--version"], capture_output=True, text=True,
                                                         timeout=30).stdout.strip()).group(1))
    except (OSError, AttributeError, ValueError, subprocess.SubprocessError):
        major = 8
    major = max(major, 8)
    return f"net{major}.0", f"{major}.*"


def scaffold(key: str) -> dict[str, str]:
    """Scaffold files for a stack with environment-dependent placeholders filled in."""
    files = dict(STACKS[key]["scaffold"])
    if key == "csharp":
        tfm, pkg = _dotnet_versions()
        files = {p: c.replace("{dotnet_tfm}", tfm).replace("{dotnet_pkg}", pkg) for p, c in files.items()}
    return files


def gitignore(key: str) -> str:
    return STACKS[key]["gitignore"] + COMMON_GITIGNORE

package ai.lmctl.gcpsim;

import io.quarkus.runtime.Quarkus;
import io.quarkus.runtime.annotations.QuarkusMain;
import java.util.Set;

/** Small packaging entry point; all GCP protocols are implemented by floci-gcp. */
@QuarkusMain
public class Main {
    public static void main(String... args) {
        if (args.length == 1 && args[0].equals("--version")) {
            System.out.println("gcpsim @VERSION@ (floci-gcp @UPSTREAM@)");
            return;
        }
        if (args.length == 0 || (args.length == 1 && args[0].equals("--help"))) {
            System.out.println("""
                gcpsim — local GCP emulator built on floci-gcp
                Usage: gcpsim serve|start [--port 4588] [--host 127.0.0.1]
                             [--project gcpsim-local] [--data-dir PATH]
                             [--auth open|enforce] [--auth-config PATH]
                       gcpsim --version | --help
                Foreground server; stop with Ctrl-C. State is in memory unless --data-dir
                is supplied. Persistent state uses upstream's persistent storage mode.
                Docker-backed services default to mock mode; FLOCI_GCP_* overrides apply.
                """);
            return;
        }
        try {
            if (!Set.of("serve", "start").contains(args[0]))
                throw new IllegalArgumentException("expected serve or start");
            String host = "127.0.0.1", port = "4588", project = "gcpsim-local", data = null;
            String auth = System.getenv().getOrDefault("SIM_AUTH", "open");
            String authConfig = System.getenv("SIM_AUTH_CONFIG");
            var seen = new java.util.HashSet<String>();
            for (int i = 1; i < args.length; i += 2) {
                String key = args[i];
                if (!Set.of("--host", "--port", "--project", "--data-dir", "--auth", "--auth-config").contains(key))
                    throw new IllegalArgumentException("unknown option");
                if (!seen.add(key) || i + 1 == args.length || args[i + 1].isBlank())
                    throw new IllegalArgumentException("options require one nonempty value and cannot repeat");
                String value = args[i + 1];
                switch (key) {
                    case "--host" -> host = value;
                    case "--port" -> port = value;
                    case "--project" -> project = value;
                    case "--data-dir" -> data = value;
                    case "--auth" -> auth = value;
                    case "--auth-config" -> authConfig = value;
                }
            }
            AuthConfig.load(auth, authConfig);
            int number = Integer.parseInt(port);
            if (number < 1 || number > 65535) throw new IllegalArgumentException("port must be 1..65535");
            if (!Set.of("127.0.0.1", "0.0.0.0").contains(host))
                throw new IllegalArgumentException("host must be 127.0.0.1 or 0.0.0.0");
            if (!project.matches("[a-z][a-z0-9-]{2,62}"))
                throw new IllegalArgumentException("invalid project ID");
            System.setProperty("quarkus.http.host", host);
            System.setProperty("quarkus.http.port", port);
            System.setProperty("floci-gcp.port", port);
            System.setProperty("floci-gcp.default-project-id", project);
            System.setProperty("floci-gcp.base-url", "http://127.0.0.1:" + port);
            System.setProperty("quarkus.banner.enabled", "false");
            System.setProperty("quarkus.log.console.stderr", "true");
            for (String service : Set.of("kafka", "cloudsql", "cloudrun", "gke", "bigquery")) {
                if (System.getenv("FLOCI_GCP_SERVICES_" + service.toUpperCase(java.util.Locale.ROOT) + "_MOCK") == null)
                    System.setProperty("floci-gcp.services." + service + ".mock", "true");
            }
            if (data != null) {
                System.setProperty("floci-gcp.storage.mode", "persistent");
                System.setProperty("floci-gcp.storage.persistent-path", data);
            }
        } catch (IllegalArgumentException e) {
            System.out.println("{\"ok\":false,\"error\":\"invalid command or option; see --help\"}");
            System.exit(2);
            return;
        }
        Quarkus.run(null, (code, failure) -> {
            if (code != 0 || failure != null) {
                if (failure != null) failure.printStackTrace(System.err);
                System.out.println("{\"ok\":false,\"error\":\"emulator failed; see stderr\"}");
                System.exit(3);
            }
        }, args);
    }
}

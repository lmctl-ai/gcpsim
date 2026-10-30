package ai.lmctl.gcpsim;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import io.floci.gcp.services.iam.IamBinding;
import io.floci.gcp.services.iam.IamPolicy;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.regex.Pattern;

/** Local fixture configuration, not a production OAuth verifier or Google IAM policy format. */
public final class AuthConfig {
    static boolean enforce;
    static Map<String, String> tokens = Map.of();
    static Map<String, IamPolicy> bindings = Map.of();
    static List<Rule> rules = List.of();
    static boolean hasPolicies;

    static void load(String mode, String file) {
        if (!Set.of("open", "enforce").contains(mode)) { throw new IllegalArgumentException(); }
        enforce = mode.equals("enforce");
        if (!enforce && file == null) { return; }
        try {
            JsonNode root = new ObjectMapper().readTree(Files.readString(Path.of(file)));
            fields(root, Set.of("tokens", "Statement", "policies"));
            JsonNode credentials = root.path("tokens");
            if (!credentials.isObject() || credentials.isEmpty()) { throw new IllegalArgumentException(); }
            Map<String, String> parsedTokens = new HashMap<>();
            credentials.properties().forEach(e -> {
                if (!e.getKey().matches("[A-Za-z0-9._~+/-]+=*") || !e.getValue().isTextual()
                        || !e.getValue().asText().matches("(user|serviceAccount):[^\\s]+")) { throw new IllegalArgumentException(); }
                parsedTokens.put(e.getKey(), e.getValue().asText());
            });
            List<Rule> parsedRules = new ArrayList<>();
            if (root.has("Statement")) {
                if (!root.get("Statement").isArray()) { throw new IllegalArgumentException(); }
                for (JsonNode rule : root.get("Statement")) {
                    fields(rule, Set.of("Effect", "Action", "ActionGlob", "ResourceGlob", "Principal"));
                    String effect = required(rule, "Effect");
                    if (!Set.of("Allow", "Deny").contains(effect) || rule.has("Action") == rule.has("ActionGlob")) { throw new IllegalArgumentException(); }
                    parsedRules.add(new Rule(effect.equals("Deny"), glob(required(rule, rule.has("Action") ? "Action" : "ActionGlob")),
                            glob(required(rule, "ResourceGlob")), glob(rule.has("Principal") ? required(rule, "Principal") : "*")));
                }
            }
            Map<String, IamPolicy> parsedBindings = new HashMap<>();
            if (root.has("policies")) {
                if (!root.get("policies").isObject()) { throw new IllegalArgumentException(); }
                for (Map.Entry<String, JsonNode> entry : root.get("policies").properties()) {
                    fields(entry.getValue(), Set.of("bindings"));
                    if (!entry.getValue().path("bindings").isArray()) { throw new IllegalArgumentException(); }
                    List<IamBinding> list = new ArrayList<>();
                    for (JsonNode binding : entry.getValue().get("bindings")) {
                        fields(binding, Set.of("role", "members"));
                        if (!binding.path("members").isArray()) { throw new IllegalArgumentException(); }
                        List<String> members = new ArrayList<>();
                        for (JsonNode member : binding.get("members")) {
                            if (!member.isTextual() || member.asText().isBlank()) { throw new IllegalArgumentException(); }
                            members.add(member.asText());
                        }
                        list.add(new IamBinding(required(binding, "role"), members, null));
                    }
                    parsedBindings.put(entry.getKey(), new IamPolicy(1, list, ""));
                }
            }
            tokens = Map.copyOf(parsedTokens);
            rules = List.copyOf(parsedRules);
            bindings = Map.copyOf(parsedBindings);
            hasPolicies = root.has("Statement") || root.has("policies");
        } catch (Exception e) {
            // Never include configuration contents, tokens, parser snippets or file paths in errors.
            throw new IllegalArgumentException("invalid auth configuration");
        }
    }

    private static void fields(JsonNode node, Set<String> allowed) {
        if (!node.isObject()) { throw new IllegalArgumentException(); }
        node.fieldNames().forEachRemaining(name -> { if (!allowed.contains(name)) { throw new IllegalArgumentException(); } });
    }
    private static String required(JsonNode node, String key) {
        if (!node.path(key).isTextual() || node.path(key).asText().isBlank()) { throw new IllegalArgumentException(); }
        return node.get(key).asText();
    }
    private static Pattern glob(String value) {
        StringBuilder pattern = new StringBuilder("^");
        for (char c : value.toCharArray()) { pattern.append(c == '*' ? ".*" : c == '?' ? "." : Pattern.quote(String.valueOf(c))); }
        return Pattern.compile(pattern.append('$').toString(), Pattern.DOTALL);
    }
    record Rule(boolean deny, Pattern action, Pattern resource, Pattern principal) {
        boolean matches(String a, String r, String p) {
            return action.matcher(a).matches() && resource.matcher(r).matches() && principal.matcher(p).matches();
        }
    }
}

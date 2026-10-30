package ai.lmctl.gcpsim;

import com.fasterxml.jackson.databind.ObjectMapper;

public final class ErrorJsonTest {
    public static void main(String[] args) throws Exception {
        String status = "quoted \"雪\" \\ newline\n %s ${opaque}";
        String message = "\"},\"injected\":true,\"error\":{\"message\":\"";
        var root = new ObjectMapper().readTree(ErrorJson.render(403, status, message));
        if (root.size() != 1 || root.path("error").size() != 3
                || root.path("error").path("code").asInt() != 403
                || !root.path("error").path("status").asText().equals(status)
                || !root.path("error").path("message").asText().equals(message)) {
            throw new AssertionError("Error template changed the JSON structure or values");
        }
        System.out.println("PASS: error template preserves quotes, Unicode, controls and opaque placeholders");
    }
}

package ai.lmctl.gcpsim;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;

/** JSON values are encoded independently; inserted text is never interpreted as a template. */
final class ErrorJson {
    private static final ObjectMapper JSON = new ObjectMapper();
    private ErrorJson() {}

    static String render(int code, String status, String message) {
        return """
                {"error":{"code":%d,"status":%s,"message":%s}}
                """.formatted(code, value(status), value(message));
    }

    private static String value(String text) {
        try {
            return JSON.writeValueAsString(text);
        } catch (JsonProcessingException impossible) {
            throw new IllegalStateException("Cannot encode error response", impossible);
        }
    }
}

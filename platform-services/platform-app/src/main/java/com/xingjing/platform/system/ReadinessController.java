package com.xingjing.platform.system;

import java.util.Map;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/v1/system")
public class ReadinessController {
    @GetMapping("/readiness")
    public Map<String, String> readiness() {
        return Map.of("service", "xingjing-platform", "status", "UP");
    }
}

package com.xingjing.platform.api;

import com.xingjing.platform.core.UuidV7;
import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import java.io.IOException;
import org.springframework.http.HttpHeaders;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

@Component
public class RequestIdFilter extends OncePerRequestFilter {
    public static final String ATTRIBUTE = RequestIdFilter.class.getName() + ".requestId";
    public static final String HEADER = "X-Request-Id";

    @Override
    protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response, FilterChain filterChain)
            throws ServletException, IOException {
        String supplied = request.getHeader(HEADER);
        String requestId = supplied == null || supplied.isBlank() ? UuidV7.randomUuid().toString() : supplied.trim();
        request.setAttribute(ATTRIBUTE, requestId);
        response.setHeader(HEADER, requestId);
        filterChain.doFilter(request, response);
    }

    public static String requestId(HttpServletRequest request) {
        Object value = request.getAttribute(ATTRIBUTE);
        if (value instanceof String requestId) {
            return requestId;
        }
        String supplied = request.getHeader(HEADER);
        String requestId = supplied == null || supplied.isBlank() ? UuidV7.randomUuid().toString() : supplied.trim();
        request.setAttribute(ATTRIBUTE, requestId);
        return requestId;
    }
}

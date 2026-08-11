package com.xingjing.platform.identity;

import com.xingjing.platform.api.ApiErrorEnvelope;
import com.xingjing.platform.api.RequestIdFilter;
import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import java.io.IOException;
import java.util.List;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.security.authentication.UsernamePasswordAuthenticationToken;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;
import tools.jackson.databind.ObjectMapper;

@Component
class BearerTokenAuthenticationFilter extends OncePerRequestFilter {
    private static final List<String> ANONYMOUS_PATHS = List.of(
            "/api/v1/system/readiness",
            "/api/v1/auth/register",
            "/api/v1/auth/login",
            "/api/v1/auth/oidc",
            "/api/v1/auth/refresh",
            "/api/v1/auth/password-reset/request",
            "/api/v1/auth/password-reset/confirm");

    private final IdentityRepository repository;
    private final ObjectMapper objectMapper;

    BearerTokenAuthenticationFilter(IdentityRepository repository, ObjectMapper objectMapper) {
        this.repository = repository;
        this.objectMapper = objectMapper;
    }

    @Override
    protected boolean shouldNotFilter(HttpServletRequest request) {
        return ANONYMOUS_PATHS.contains(request.getRequestURI());
    }

    @Override
    protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response, FilterChain filterChain)
            throws ServletException, IOException {
        String authorization = request.getHeader(HttpHeaders.AUTHORIZATION);
        if (authorization != null && !authorization.isBlank()) {
            if (!authorization.startsWith("Bearer ") || authorization.length() <= "Bearer ".length()) {
                writeUnauthenticated(response, request);
                return;
            }
            SessionPrincipal principal = repository.findActiveSessionByAccessTokenHash(
                    TokenHasher.sha256(authorization.substring("Bearer ".length()).trim()));
            if (principal == null) {
                writeUnauthenticated(response, request);
                return;
            }
            SecurityContextHolder.getContext().setAuthentication(
                    UsernamePasswordAuthenticationToken.authenticated(principal, null, List.of()));
        }
        try {
            filterChain.doFilter(request, response);
        } finally {
            SecurityContextHolder.clearContext();
        }
    }

    private void writeUnauthenticated(HttpServletResponse response, HttpServletRequest request) throws IOException {
        String requestId = RequestIdFilter.requestId(request);
        response.setStatus(HttpServletResponse.SC_UNAUTHORIZED);
        response.setContentType(MediaType.APPLICATION_JSON_VALUE);
        response.setHeader(RequestIdFilter.HEADER, requestId);
        objectMapper.writeValue(response.getOutputStream(), ApiErrorEnvelope.unauthenticated(requestId));
    }
}

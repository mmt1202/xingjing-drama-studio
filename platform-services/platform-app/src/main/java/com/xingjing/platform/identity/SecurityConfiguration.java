package com.xingjing.platform.identity;

import com.xingjing.platform.api.ApiErrorEnvelope;
import com.xingjing.platform.api.RequestIdFilter;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import java.io.IOException;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.http.MediaType;
import org.springframework.security.config.annotation.web.builders.HttpSecurity;
import org.springframework.security.config.http.SessionCreationPolicy;
import org.springframework.security.crypto.bcrypt.BCryptPasswordEncoder;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.security.web.SecurityFilterChain;
import org.springframework.security.web.authentication.UsernamePasswordAuthenticationFilter;
import tools.jackson.databind.ObjectMapper;

@Configuration
class SecurityConfiguration {
    @Bean
    PasswordEncoder passwordEncoder() {
        return new BCryptPasswordEncoder();
    }

    @Bean
    SecurityFilterChain securityFilterChain(
            HttpSecurity http, ObjectMapper objectMapper, BearerTokenAuthenticationFilter bearerTokenAuthenticationFilter)
            throws Exception {
        http.csrf(csrf -> csrf.disable())
                .sessionManagement(session -> session.sessionCreationPolicy(SessionCreationPolicy.STATELESS))
                .authorizeHttpRequests(authorize -> authorize
                        .requestMatchers(
                                "/api/v1/system/readiness",
                                "/api/v1/auth/register",
                                "/api/v1/auth/login",
                                "/api/v1/auth/oidc",
                                "/api/v1/auth/refresh",
                                "/api/v1/auth/password-reset/request",
                                "/api/v1/auth/password-reset/confirm")
                        .permitAll()
                        .anyRequest()
                        .authenticated())
                .exceptionHandling(exceptions -> exceptions.authenticationEntryPoint(
                        (request, response, exception) -> writeUnauthenticated(response, request, objectMapper)))
                .httpBasic(httpBasic -> httpBasic.disable())
                .addFilterBefore(bearerTokenAuthenticationFilter, UsernamePasswordAuthenticationFilter.class);
        return http.build();
    }

    private static void writeUnauthenticated(
            HttpServletResponse response, HttpServletRequest request, ObjectMapper objectMapper) throws IOException {
        String requestId = RequestIdFilter.requestId(request);
        response.setStatus(HttpServletResponse.SC_UNAUTHORIZED);
        response.setContentType(MediaType.APPLICATION_JSON_VALUE);
        response.setHeader(RequestIdFilter.HEADER, requestId);
        objectMapper.writeValue(response.getOutputStream(), ApiErrorEnvelope.unauthenticated(requestId));
    }
}

import type enAuth from "@/i18n/en/auth";

export default {
  'login': 'Đăng nhập',
  'logging_in': 'Đang đăng nhập...',
  'login_failed': 'Đăng nhập thất bại',
  'workspace_context_failed': 'Danh tính đã được xác minh nhưng chưa xác nhận được không gian làm việc. Vui lòng đăng nhập lại.',
  'username': 'Tên đăng nhập',
  'password': 'Mật khẩu',
} satisfies Record<keyof typeof enAuth, string>;

// The same rules user-service applies to POST /auth/register, checked before sending.
// The password is never trimmed or changed.

export interface RegisterForm {
  fullName: string;
  email: string;
  password: string;
  confirmPassword: string;
}

export type RegisterErrors = Partial<Record<keyof RegisterForm, string>>;

const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

/** bcrypt uses at most 72 bytes; letters with accents take 2 bytes in UTF-8 */
export function utf8Length(text: string): number {
  return new TextEncoder().encode(text).length;
}

export function validateRegistration(form: RegisterForm): RegisterErrors {
  const errors: RegisterErrors = {};
  const name = form.fullName.trim();
  if (name.length < 2) errors.fullName = 'Escribe tu nombre (al menos 2 caracteres).';
  else if (name.length > 120) errors.fullName = 'El nombre puede tener como máximo 120 caracteres.';

  if (!EMAIL.test(form.email.trim())) errors.email = 'Escribe un correo válido, por ejemplo nombre@dominio.com.';

  if (form.password.length < 8) errors.password = 'La contraseña debe tener al menos 8 caracteres.';
  else if (utf8Length(form.password) > 72) {
    errors.password = 'La contraseña es demasiado larga (máximo 72 bytes; las letras con tilde cuentan doble).';
  }

  if (form.confirmPassword !== form.password) errors.confirmPassword = 'Las contraseñas no coinciden.';
  return errors;
}

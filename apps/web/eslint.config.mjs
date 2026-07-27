// Flat config (ESLint 9+). `next lint` was removed in Next 16, so the lint
// script runs ESLint directly and this file is what it reads.
//
// The rule set is small on purpose: TypeScript in strict mode already rejects
// most of what a general JavaScript rule set catches, and the rules kept here
// are the ones that catch mistakes the compiler cannot see — chiefly React hook
// dependencies, where a missing entry produces a component that renders stale
// data rather than an error.

import js from '@eslint/js';
import reactHooks from 'eslint-plugin-react-hooks';
import tseslint from 'typescript-eslint';

export default tseslint.config(
  { ignores: ['.next/**', 'node_modules/**', 'playwright-report/**', 'test-results/**'] },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    files: ['src/**/*.{ts,tsx}'],
    plugins: { 'react-hooks': reactHooks },
    rules: {
      ...reactHooks.configs.recommended.rules,
      // Unused arguments are how a callback documents the signature it must
      // match; a leading underscore marks the ones deliberately ignored.
      '@typescript-eslint/no-unused-vars': [
        'error',
        { argsIgnorePattern: '^_', varsIgnorePattern: '^_' },
      ],
    },
  },
  {
    files: ['tests/**/*.{ts,tsx}'],
    rules: { '@typescript-eslint/no-explicit-any': 'off' },
  },
);

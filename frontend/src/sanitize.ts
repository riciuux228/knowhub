import DOMPurify from 'dompurify';

export const sanitize = (html: string): string =>
  DOMPurify.sanitize(html, {
    ADD_ATTR: ['target', 'rel'],
    ALLOW_DATA_ATTR: false,
  });

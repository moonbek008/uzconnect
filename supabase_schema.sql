-- EduConnect UZ — Supabase Schema
-- Run this in Supabase Dashboard → SQL Editor

create table users (
  user_id     bigint primary key,
  role        text not null check (role in ('student', 'teacher')),
  nickname    text not null,
  subjects    text[] default '{}',
  reputation  int default 0,
  star_badge  boolean default false,
  registered_at timestamptz default now()
);

create table questions (
  id              bigserial primary key,
  student_id      bigint not null references users(user_id),
  subject         text not null,
  text            text not null,
  photo_file_id   text,
  status          text default 'open' check (status in ('open', 'solved')),
  channel_msg_id  bigint,
  nickname        text not null,
  posted_at       timestamptz default now(),
  solved_at       timestamptz
);

create table answers (
  id           bigserial primary key,
  question_id  bigint not null references questions(id),
  teacher_id   bigint not null references users(user_id),
  text         text not null,
  feedback     text check (feedback in ('foydali', 'tushunmadim', 'toliq_emas', 'notogri')),
  answered_at  timestamptz default now()
);

create table reports (
  id           bigserial primary key,
  reporter_id  bigint not null,
  question_id  bigint not null references questions(id),
  reported_at  timestamptz default now()
);

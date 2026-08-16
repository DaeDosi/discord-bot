"use client";
import { useState } from "react";

// 스트리머 프로필 썸네일.
//
// ── 왜 공용으로 뺐나 (UI-R 요구 3) ──────────────────────────────────────────
// 랭킹 표마다 `<img … loading="lazy">`가 복사돼 있었고, **첫 화면에 보이는 행까지
// 전부 lazy**였다. 브라우저는 뷰포트 안의 lazy 이미지도 결국 받아 오지만
// 레이아웃이 끝난 뒤 낮은 우선순위로 큐에 넣으므로, 목록 글자가 먼저 뜨고
// 얼굴이 뒤늦게 채워지는 모습이 그대로 보였다.
//
// 여기서 지키는 계약:
//  · **첫 화면 행만** `eager` + `fetchPriority="high"` — 나머지는 lazy 유지.
//    전부 eager로 바꾸면 수백 장이 한꺼번에 경쟁해 오히려 느려진다.
//  · `decoding="async"` — 디코딩이 메인 스레드를 막지 않게 한다.
//  · 컨테이너가 **크기를 미리 차지**한다(w-6 h-6 고정) → layout shift 없음.
//  · URL이 없거나 로드에 실패하면 **깨진 이미지 아이콘 대신** 기존 회색 원을 남긴다.
//  · 같은 URL은 브라우저 HTTP 캐시가 재사용한다 — 별도 메모리 캐시를 만들지 않는다
//    (목록이 다시 그려져도 `src`가 같으면 재요청이 나가지 않는다).
//
// Next `<Image>`를 쓰지 않는 이유: 원본이 치지직 CDN이라 최적화 프록시를 태우면
// 우리 서버를 한 번 더 거치게 되고, 이 저장소는 그 도메인을 `images.remotePatterns`에
// 등록하지 않았다. 프록시 없이 원본을 직접 받는 편이 첫 표시가 빠르다.

/** 이 개수까지의 행만 즉시 로드한다. 1440px 기준 첫 화면에 들어오는 행 수. */
export const EAGER_ROWS = 12;

export default function StreamerAvatar({
  src, alt = "", index, size = 24, ringStyle, className = "",
}: {
  src?: string | null;
  alt?: string;
  /** 목록에서의 순서(0부터). 첫 화면 여부 판단에만 쓴다. */
  index: number;
  size?: number;
  ringStyle?: React.CSSProperties;
  className?: string;
}) {
  const [failed, setFailed] = useState(false);
  const eager = index < EAGER_ROWS;
  const show = !!src && !failed;

  return (
    <span
      className={`inline-block overflow-hidden rounded-full bg-bg-hover shrink-0 ${className}`}
      style={{ width: size, height: size, ...ringStyle }}
      aria-hidden={alt ? undefined : true}
    >
      {show && (
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={src}
          alt={alt}
          width={size}
          height={size}
          loading={eager ? "eager" : "lazy"}
          fetchPriority={eager ? "high" : "auto"}
          decoding="async"
          onError={() => setFailed(true)}
          className="h-full w-full object-cover"
        />
      )}
    </span>
  );
}

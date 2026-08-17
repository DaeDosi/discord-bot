import type { Metadata } from "next";
import Link from "next/link";

import Footer from "@/components/Footer";

// 통계 안내 페이지.
//
// 왜 만들었나: 같은 설명이 통계 메인 하단과 스트리머 상세 하단 두 곳에 통째로
// 박혀 있었다. 내용이 겹치는데다(수집 주기·비제휴 고지·법적 링크가 양쪽에 중복),
// 스트리머 상세 쪽은 채널 이름과 그때의 수치가 문장에 섞여 있어 페이지마다 다른
// 글이 됐다. 설명은 한 곳에 두고 두 화면은 링크만 건다.
//
// 왜 전역 `/guide`가 아닌가: 그쪽은 **디스코드 봇 사용 가이드**다. 통계 읽는 법을
// 거기에 합치면 서로 다른 두 제품의 설명이 한 페이지에 섞인다.
//
// 여기에는 특정 스트리머 이름이나 그 시점의 수치를 넣지 않는다 — 넣는 순간 이
// 페이지도 다시 "페이지마다 달라지는 글"이 된다.

export const metadata: Metadata = {
  title: "통계 안내 — NexBot 치지직 방송 통계",
  description:
    "NexBot 치지직 통계의 데이터 수집 방식과 지표 읽는 법을 정리했습니다. "
    + "동시 시청자·뷰어십·카테고리·활동 잔디의 의미와 수집 주기로 인한 오차를 설명합니다.",
  alternates: { canonical: "/stats/guide" },
  openGraph: {
    type: "article",
    url: "/stats/guide",
    title: "통계 안내 — NexBot 치지직 방송 통계",
    description: "치지직 통계 지표를 읽는 방법과 데이터 수집 방식 안내",
  },
};

function Section({ id, title, children }: {
  id: string; title: string; children: React.ReactNode;
}) {
  return (
    <section id={id} className="scroll-mt-20">
      <h2 className="text-lg font-bold text-fg md:text-xl">{title}</h2>
      <div className="mt-3 space-y-3 text-sm leading-relaxed text-muted">{children}</div>
    </section>
  );
}

export default function StatsGuidePage() {
  return (
    <div className="flex min-h-screen flex-col bg-bg text-fg">
      <main className="mx-auto w-full max-w-3xl flex-1 px-5 py-10 md:py-14">
        <nav aria-label="위치" className="text-[13px] text-muted">
          <Link href="/stats" className="hover:text-fg">치지직 통계</Link>
          <span className="mx-1.5">/</span>
          <span className="text-fg">통계 안내</span>
        </nav>

        <h1 className="mt-3 text-2xl font-extrabold tracking-tight md:text-3xl">
          치지직 통계 안내
        </h1>
        <p className="mt-3 text-sm leading-relaxed text-muted">
          NexBot 치지직 통계는 치지직(CHZZK)에서 방송하는 스트리머가 자신의 방송 성과를
          객관적인 수치로 확인하고 다음 방송 전략을 세울 수 있도록 만든 트래픽 분석
          도구입니다. 방송을 오래 해도 오늘 사람이 많았는지 적었는지는 감으로만 판단하게
          되기 쉽고, 이제 시작한 채널은 비교 기준이 될 데이터가 없어 성장하고 있는지조차
          알기 어렵습니다. 이 서비스는 치지직이 공개하는 라이브 방송 목록을 주기적으로
          수집해 시간에 따른 변화를 기록합니다.
        </p>

        <div className="mt-10 space-y-10">
          <Section id="collect" title="데이터를 어떻게 모으나">
            <p>
              치지직이 공개하는 라이브 방송 목록을 <b className="text-fg">약 10분 간격</b>으로
              수집합니다. 화면의 값은 마지막 수집 시점의 값이며, 각 화면에 그 시각을 함께
              적어 두었습니다. 따라서 <b className="text-fg">&ldquo;실시간&rdquo;은 매초 완전히
              동기화된다는 뜻이 아닙니다.</b> 방금 시작하거나 끝난 방송, 주기 사이에 잠깐
              켜졌다 꺼진 방송은 아직 반영되지 않았을 수 있습니다.
            </p>
            <p>
              기능마다 주기가 다릅니다. 이벤트 클립의 신규 탐색은 더 짧은 주기로 돌고,
              클립의 하트·조회수는 정해진 시각이 아니라 계속 이어지는 방식으로 갱신합니다.
              대상 클립 수와 외부 API 요청 제한에 따라 한 클립이 다시 갱신되기까지 걸리는
              시간이 달라집니다. 이 값들은 운영 설정에 따라 바뀔 수 있어 고정된 보장이
              아닙니다.
            </p>
          </Section>

          <Section id="metrics" title="주요 지표의 뜻">
            <ul className="list-disc space-y-1.5 pl-5">
              <li><b className="text-fg">현재 라이브 방송</b> — 가장 최근 수집 시점에 라이브로 확인된 채널 수입니다.</li>
              <li><b className="text-fg">전체 동시 시청자</b> — 그 시점에 수집된 라이브 채널들의 시청자 수를 모두 더한 값입니다.</li>
              <li><b className="text-fg">방송당 평균 시청자</b> — 전체 동시 시청자를 라이브 방송 수로 나눈 값입니다. 큰 방송 하나가 평균을 끌어올릴 수 있으므로 순위와 함께 보는 편이 정확합니다.</li>
              <li><b className="text-fg">뷰어십(누적 시청 시간)</b> — 각 수집 시점의 시청자 수에 수집 간격을 곱해 더한 값으로, 단위는 시청자-시간입니다. 100명이 1시간 본 방송과 50명이 2시간 본 방송이 같은 값이 됩니다.</li>
              <li><b className="text-fg">신규 스트리머</b> — 첫 방송 확인일로부터 60일 이내인 채널입니다. 시청자 규모와 무관합니다.</li>
              <li><b className="text-fg">소형 스트리머</b> — 최근 평균 동시 시청자가 10명 이하인 채널입니다. 경력과 무관하므로 신규 그룹과 겹칠 수 있습니다.</li>
            </ul>
          </Section>

          <Section id="overall" title="전체 통계 화면 읽는 법">
            <p>
              화면마다 답하는 질문이 다릅니다.
            </p>
            <ul className="list-disc space-y-1.5 pl-5">
              <li><b className="text-fg">랭킹</b> — &ldquo;지금 누가 많이 보고 있는가.&rdquo; 기본은 동시 시청자 기준이며 팔로워·방송 시간으로 정렬을 바꿀 수 있습니다.</li>
              <li><b className="text-fg">카테고리 분석</b> — &ldquo;어디에 시청자가 모여 있고, 어디가 덜 붐비는가.&rdquo; 시청자 수와 방송 수를 함께 봐야 판단이 됩니다. 시청자가 아무리 많아도 방송이 그보다 더 많으면 목록에서 묻히기 쉽습니다.</li>
              {/* 메뉴가 신규/소형 두 개로 갈렸다. 안내도 같이 갈라 적는다 —
                  한 줄로 묶어 두면 어느 메뉴를 말하는지 화면에서 찾을 수 없다.
                  두 기준이 서로를 배제하지 않는다는 점도 여기서 밝힌다. */}
              <li><b className="text-fg">신규 스트리머 통계</b> — &ldquo;막 시작한 채널 중 지금 반응이 오는 곳은 어디인가.&rdquo; 첫 방송 후 60일 이내가 기준이며 시청자 규모는 보지 않습니다.</li>
              <li><b className="text-fg">소형 스트리머 통계</b> — &ldquo;아직 규모가 작은 채널은 지금 어떤 상황인가.&rdquo; 최근 7일 평균 동시 시청자 10명 이하가 기준이며 방송 경력은 보지 않습니다. 두 기준은 서로 독립적이라 양쪽에 함께 나오는 채널이 있습니다.</li>
              <li><b className="text-fg">기간 분석</b> — &ldquo;이 기간 전체로 보면 어땠는가.&rdquo; 누적 시청 시간은 기간이 길수록 커지므로, 방송 하나의 성적은 평균 시청자로 함께 확인하시기 바랍니다.</li>
            </ul>
            <p>
              방송 시작 시간을 정할 때는 노출 최적 시간대를 참고하시기 바랍니다. 시청자가 가장
              많은 피크 타임은 동시에 경쟁 방송도 가장 많은 시간이므로, 규모가 작은 채널이라면
              방송당 평균 시청자가 높게 유지되는 구간을 노리는 편이 신규 유입에 유리할 수 있습니다.
            </p>
          </Section>

          <Section id="streamer" title="스트리머 상세 통계 읽는 법">
            <p>
              평균 동시 시청자와 최고 동시 시청자는 방송의 현재 체급을 보여 주는 기본
              지표입니다. <b className="text-fg">방송 시간</b>은 수집된 스냅샷을 기준으로 추정한
              실제 송출 시간이며, <b className="text-fg">시청 시간</b>은 동시 시청자 수와 방송
              시간을 곱해 계산합니다. 같은 평균 시청자라도 방송을 길게 유지하면 시청 시간이
              늘어나므로, 두 값을 함께 보면 시청자 수와 방송 길이 중 무엇이 성과를 만들었는지
              구분할 수 있습니다.
            </p>
            <p>
              <b className="text-fg">카테고리 비중</b>은 어떤 게임이나 콘텐츠에 방송 시간을 얼마나
              배분했는지 보여 주며, 특정 카테고리에 편중되어 있는지 여러 콘텐츠를 병행하는지
              판단하는 근거가 됩니다. <b className="text-fg">활동 잔디</b>는 최근 방송 빈도와 공백
              구간을 한눈에 보여 주므로 방송 주기가 일정한지 점검할 때 유용합니다. 칸이 진할수록
              선택한 지표가 높은 날이고, 요일과 월은 한국 시간(KST) 기준으로 표시합니다.
            </p>
            <p>
              절대 수치를 다른 채널과 직접 비교하기보다는, 같은 채널의 시간에 따른 변화 추세를
              보는 용도로 활용하시길 권합니다. 다른 채널과의 상대적인 위치가 궁금하다면{" "}
              <Link href="/stats" className="text-accent hover:underline">치지직 전체 방송 통계</Link>{" "}
              페이지의 랭킹과 카테고리 점유율을 함께 참고하시기 바랍니다.
            </p>
          </Section>

          <Section id="accuracy" title="실제 치지직 화면과 다를 수 있는 이유">
            <p>
              수집 시점 차이가 가장 큰 이유입니다. 그 밖에 원본 API가 일시적으로 응답하지 않거나
              값을 나중에 수정하는 경우, 일부 항목만 내려 주는 경우에도 차이가 생깁니다. 방송
              시간과 시청 시간은 수집 간격 단위로 근사되며, 수집 공백이 있었다면 실제보다 낮게
              집계될 수 있습니다. 과거 이력은 NexBot이 수집을 시작한 이후 구간만 존재하며 그
              이전은 복원할 수 없습니다. 수치가 실제와 다르다고 판단되시면{" "}
              <Link href="/contact" className="text-accent hover:underline">문의 페이지</Link>로
              알려 주시기 바랍니다.
            </p>
          </Section>

          <Section id="notice" title="비공식 서비스 안내">
            <p>
              NexBot은 치지직이 공개하는 정보를 자체 수집·가공한{" "}
              <b className="text-fg">비공식 서비스</b>로, 네이버 및 치지직과 제휴 관계가 없습니다.
              이 페이지의 어떤 수치도 치지직의 공식 심사나 평가 결과가 아닙니다. 이벤트 관련
              집계 역시 NexBot이 공개 지표로 계산한 값이며 공식 순위와 무관합니다.
            </p>
            <p>
              서비스 이용과 데이터 처리에 관한 내용은{" "}
              <Link href="/terms" className="text-accent hover:underline">이용약관</Link>과{" "}
              <Link href="/privacy" className="text-accent hover:underline">개인정보처리방침</Link>
              에서 확인하실 수 있습니다.
            </p>
          </Section>
        </div>

        <div className="mt-12 border-t border-border pt-6">
          <Link href="/stats" className="btn-secondary nb-tap inline-flex text-sm">
            통계로 돌아가기
          </Link>
        </div>
      </main>
      <Footer />
    </div>
  );
}

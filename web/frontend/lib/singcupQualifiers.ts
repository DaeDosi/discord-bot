// 싱드컵 갤럭시 시즌 — 치지직 **공식 예선 참가자 명단**(정적·버전 고정).
//
// 출처는 치지직 공식 라운지 공지 하나뿐이고, 이 파일은 그 공지의 SmartEditor 문서
// JSON에 실린 표 3개에서 **설계 시점에 1회** 추출한 결과다. 런타임에 네이버를
// 다시 긁지 않는다 — 대회 명단은 발표 뒤 바뀌지 않는 확정 값이라 매번 가져올
// 이유가 없고, 라운지 API는 한국 IP 밖에서 막히므로 런타임 의존이 곧 장애다.
//
// 정본 원본(JSON)과 재생성 방법은 저장소의
// `docs/data/singcup2_qualifiers_galaxy-2026.json`에 있다. 이 파일을 손으로 고치지
// 말고 그 JSON을 고친 뒤 다시 생성할 것. 두 파일이 어긋나면
// `__tests__/singcupQualifiers.test.ts`가 개수·중복·형식으로 잡아낸다.
//
// **중요한 명칭 구분** — 여기의 "예선 그룹 엔트리"(teamNumber)는 이번 싱드컵에 함께
// 나가는 팀이고, 대시보드의 "소속 그룹"(스텔라이브·인첸트 등 `team_tags`)은 상시
// 소속이다. 서로 다른 개념이므로 UI에서도 타입에서도 섞지 않는다.
//
// 참가자 수를 화면에 쓸 때 주의: 여성 64 + 남성 64 + 그룹 멤버 링크 73 = 201이지만
// **"총 201명"으로 표기하지 않는다**. 73은 인원수가 아니라 공지에 실린 링크 항목
// 수이고, 그룹은 32팀으로 세는 것이 공식 발표의 단위다.

export type QualifierCategory = "female_solo" | "male_solo";

export interface QualifierSolo {
  /** 공지의 표에 실린 순서(1부터). 순위가 아니다 — 정렬 근거로만 쓴다. */
  officialOrder: number;
  /** 공식 발표 시점의 이름. 채널명이 그 뒤 바뀌었을 수 있어 현재 이름과 함께 보여준다. */
  announcedName: string;
  channelId: string;
}

export interface QualifierGroupMember {
  memberOrder: number;
  announcedName: string;
  channelId: string;
}

export interface QualifierGroup {
  /** 공지의 팀 번호(1~32, 연속). */
  teamNumber: number;
  groupEntryId: string;
  members: QualifierGroupMember[];
}

const FEMALE_SOLO: QualifierSolo[] = [
  { officialOrder: 1, announcedName: "고다요", channelId: "54cf8e05daaaa9577ad0f211d495dc95" },
  { officialOrder: 2, announcedName: "김아테 l Ate", channelId: "f42e97f59c3177b8686dccfbf90792dd" },
  { officialOrder: 3, announcedName: "냐오 NYAO", channelId: "79ef1f5274b48fcb2de41b8ac8ea7ca1" },
  { officialOrder: 4, announcedName: "리모 RIMO", channelId: "4a0493dd6f3542b99943c39848ad1045" },
  { officialOrder: 5, announcedName: "린시", channelId: "54bdb327ed6039db869d15b0e5eec394" },
  { officialOrder: 6, announcedName: "마무 Mamuu", channelId: "902744ac298720925b661711d76f7133" },
  { officialOrder: 7, announcedName: "마우쥐", channelId: "b2854dc0735e55fa86c53bd15242d30f" },
  { officialOrder: 8, announcedName: "매화린 Maehwarin", channelId: "c62ac389ebfc8942302298df5e9cd71b" },
  { officialOrder: 9, announcedName: "모이 M0I", channelId: "be059789e19ac3e46198e15911a80122" },
  { officialOrder: 10, announcedName: "모코 파르페", channelId: "d20f164fe5946abe0bce2e5a43052923" },
  { officialOrder: 11, announcedName: "미네르 Mineru", channelId: "aa6e91dc838cdf50996ffdb86f5854be" },
  { officialOrder: 12, announcedName: "미하루 Miharu", channelId: "088973112d8acc831ec20274f7ffbb99" },
  { officialOrder: 13, announcedName: "반 희", channelId: "0c061f7f79909af482d9e66879d2225c" },
  { officialOrder: 14, announcedName: "백희 Baekhee", channelId: "d916dc846dcbe66a3482e238625b9022" },
  { officialOrder: 15, announcedName: "비화 Bihwa", channelId: "521b37e8cd74b933501d3d1ad4d5031c" },
  { officialOrder: 16, announcedName: "산 SAN", channelId: "40132b11317951dafff8c1a770b34b63" },
  { officialOrder: 17, announcedName: "삿땅 Sattan", channelId: "cdedd10b0a6c0c007a028a4f15c82ad1" },
  { officialOrder: 18, announcedName: "샤메이", channelId: "6d16804cf98da47ba82bd13c0c029723" },
  { officialOrder: 19, announcedName: "세이로쿠 키라", channelId: "f63f66f701a9af347c668f3056cdaa9a" },
  { officialOrder: 20, announcedName: "소봄", channelId: "b3e9f222e434ae3016276987bf080eb4" },
  { officialOrder: 21, announcedName: "소우선", channelId: "8840e6587106713e56f3267a193ae6b8" },
  { officialOrder: 22, announcedName: "쇼코코 도리", channelId: "cb0ceb32cbf6bced3bac7892cbca37e1" },
  { officialOrder: 23, announcedName: "아루네 arune", channelId: "7edea32c0c19842f07c6a1e323a5b15a" },
  { officialOrder: 24, announcedName: "아마네 나기 Planeta", channelId: "941ea3807ba8b9b7dddb1670e3e7e5af" },
  { officialOrder: 25, announcedName: "아오토라 유키", channelId: "1e09ba347161a434e40b8245e0bf3cb3" },
  { officialOrder: 26, announcedName: "아호밍 AHOMING", channelId: "df0553bcb31247eaad8292166d0e421b" },
  { officialOrder: 27, announcedName: "앵차루", channelId: "5aa661567c8c14713a164759d21c036d" },
  { officialOrder: 28, announcedName: "양메이", channelId: "f1869f490ddd660c420b2f57c649e6bb" },
  { officialOrder: 29, announcedName: "연하늘 HaneuI", channelId: "b8b36da0d46acde123539f53daa23f7d" },
  { officialOrder: 30, announcedName: "오단밍", channelId: "f3b204dd3fd6925835ca1848cd4b6d3c" },
  { officialOrder: 31, announcedName: "옥우", channelId: "73d006ad5ed12000547433ed33a68247" },
  { officialOrder: 32, announcedName: "온 하루", channelId: "0f61ae00c2aef2b789dc009e51cbcc5a" },
  { officialOrder: 33, announcedName: "온치", channelId: "afe7bad3f54d9542cd3d0a30e38aa8ed" },
  { officialOrder: 34, announcedName: "유달린", channelId: "844d222a850ac5822a9a68870a24b786" },
  { officialOrder: 35, announcedName: "유레카 님", channelId: "3d5546fc8d0dcb478c973a9bc1328980" },
  { officialOrder: 36, announcedName: "유메노 시로코", channelId: "34d7c5fc0acaddc2e38053e5e54d771b" },
  { officialOrder: 37, announcedName: "유엘 Yuel", channelId: "2626f1dee4cedd89ecc8c4c313e37ecc" },
  { officialOrder: 38, announcedName: "유피 UPI", channelId: "d264bede9f1690ea1ff776309f8e2589" },
  { officialOrder: 39, announcedName: "유할매", channelId: "2c0c0ff859f6cb8045a3cdf99b3b9b54" },
  { officialOrder: 40, announcedName: "윤키키", channelId: "fedb21a1e1d5f9862eb47b15c69beadd" },
  { officialOrder: 41, announcedName: "이 늘", channelId: "1482d68b3478d2962e9d000ed6a33167" },
  { officialOrder: 42, announcedName: "이 소 에", channelId: "343c202c69ba6d11b7ec51741f9591ac" },
  { officialOrder: 43, announcedName: "진짜루비", channelId: "3d08f17e7ec1e2fb258f76c35d992160" },
  { officialOrder: 44, announcedName: "청목 Chungmok", channelId: "42344dd53d4c5dc33e962c8913f6adc3" },
  { officialOrder: 45, announcedName: "쵸꾸미", channelId: "f61107c04a4ae129873da5a5a25e2527" },
  { officialOrder: 46, announcedName: "치치 Planeta", channelId: "d5e2e0c14dcca4c4b10c7c9633022f52" },
  { officialOrder: 47, announcedName: "칠찡나", channelId: "cb2d7db1fe51bfc3e7b6ff438fd66ee3" },
  { officialOrder: 48, announcedName: "카가야키 노바", channelId: "b73696a325726b5aba0c391b4c70ab46" },
  { officialOrder: 49, announcedName: "카네코 파냐 Planeta", channelId: "5ead7124638ac4c568f2cde0224b3b6b" },
  { officialOrder: 50, announcedName: "코오리 세라 Kori Serah", channelId: "0d4e078913f04f0412f0092e10974492" },
  { officialOrder: 51, announcedName: "쿠레나이 나츠키", channelId: "a54372e8197f6d241a43a318279860d6" },
  { officialOrder: 52, announcedName: "쿠미 란케", channelId: "c21373316247091b1dfe344e878d0e41" },
  { officialOrder: 53, announcedName: "쿠뽀미", channelId: "73fc52c403eb3485524c02e1a0f2e623" },
  { officialOrder: 54, announcedName: "쿠온 레이 Planeta", channelId: "59aa824e4c4a56dd51e7a5e2e9172648" },
  { officialOrder: 55, announcedName: "키미도리 메로", channelId: "33d58e8eb2014d6e6c5eb74dcc587ebd" },
  { officialOrder: 56, announcedName: "키쿄 KIKYO", channelId: "df0f1ea25a23e05cf2ef031c2d807067" },
  { officialOrder: 57, announcedName: "테리 눈나", channelId: "0a2020b09b8cc7f2285b7ae5de2ce4d3" },
  { officialOrder: 58, announcedName: "텐플라스텐 텐텐", channelId: "88eb7dda524ca626ad3e08359b182900" },
  { officialOrder: 59, announcedName: "플레디 PLEDDY", channelId: "51211b5f8f3fe094f3740780aea32db0" },
  { officialOrder: 60, announcedName: "하렌 루베오스", channelId: "11f9a14c3439b7b1ceadd819d61624da" },
  { officialOrder: 61, announcedName: "하카세 유우", channelId: "3ab5d2f7a10442389c660ab0e4451622" },
  { officialOrder: 62, announcedName: "한량1234", channelId: "d409fe40356ee1ebfacbe2dd460ad573" },
  { officialOrder: 63, announcedName: "호시에 제로", channelId: "5786141997728cc2fcd660a15ef5f3af" },
  { officialOrder: 64, announcedName: "RED레드", channelId: "a96cea2d2c39cec636ba8170c66a0510" },
];

const MALE_SOLO: QualifierSolo[] = [
  { officialOrder: 1, announcedName: "강해온", channelId: "e24d8a6e6f04c4f113f50f38028405c7" },
  { officialOrder: 2, announcedName: "규비 KYUBI", channelId: "28cbc9a7f252b4bccf6fc479caca686e" },
  { officialOrder: 3, announcedName: "김 동 백", channelId: "8e5e48f6aff1f698c53309c4ba582553" },
  { officialOrder: 4, announcedName: "김 열 정", channelId: "7c3c0cb8f3a5498b7a2956ef42e0b414" },
  { officialOrder: 5, announcedName: "김 재 우", channelId: "4d3c00056220010aea0411b454ab57f1" },
  { officialOrder: 6, announcedName: "김카인 Cains", channelId: "f696add177b036a5cdd288748d6200e5" },
  { officialOrder: 7, announcedName: "노 아즈", channelId: "2fdf1c0fe5f498dc6857f66fa0442c8b" },
  { officialOrder: 8, announcedName: "노래맛 김쿠키", channelId: "f8f9c0d0029b58c79eb6070ff501cac1" },
  { officialOrder: 9, announcedName: "도진 YLIF", channelId: "2e9602da2a4897bcfd8e08015e2c65a5" },
  { officialOrder: 10, announcedName: "두간", channelId: "07bba51bf0a233f3f44b54431704b190" },
  { officialOrder: 11, announcedName: "들따때", channelId: "3f4c4eb3ad63464a4f4a2f28517dfd8d" },
  { officialOrder: 12, announcedName: "디안 dian", channelId: "cf6cd035dad7d3854ffcdc07ed4c77ad" },
  { officialOrder: 13, announcedName: "디온 Dion", channelId: "f380f620615fffd54451032686932f9d" },
  { officialOrder: 14, announcedName: "뚜우비", channelId: "98bf268c594aa7a14f234296eca399e4" },
  { officialOrder: 15, announcedName: "란돈", channelId: "e3d5c3218579a4902213b67d0acff24c" },
  { officialOrder: 16, announcedName: "랩 소디", channelId: "d904f6c5605818e3a34560f061d8dbcb" },
  { officialOrder: 17, announcedName: "마이시네", channelId: "1c3b5788e820a5cd622aaba8daaaf343" },
  { officialOrder: 18, announcedName: "매드해터", channelId: "94f662d396e1eca8f4d41b956a01844c" },
  { officialOrder: 19, announcedName: "모노 맥거핀", channelId: "aa4e4434b4b9ca335011a8c61b9531d3" },
  { officialOrder: 20, announcedName: "문시우 Si0w0", channelId: "14f87cd9b5114589f49deb1ab501b574" },
  { officialOrder: 21, announcedName: "미우 ME W", channelId: "1e2555cbdf56ff1add40e5c9a5b59851" },
  { officialOrder: 22, announcedName: "밀로 Millo", channelId: "e999d11761cdce088d9af3fcd2f42a86" },
  { officialOrder: 23, announcedName: "밀밭o", channelId: "78e4761c91cf528767272b4c143d0393" },
  { officialOrder: 24, announcedName: "박검복", channelId: "77a81ceffdab49f687a3e39ddf04a6fe" },
  { officialOrder: 25, announcedName: "백호범이", channelId: "41a1ae4d44647340bd2be35d5ee434d2" },
  { officialOrder: 26, announcedName: "보 라 곰", channelId: "58ff653b1ca8ff2c7f352dab48a8f9e4" },
  { officialOrder: 27, announcedName: "서노SEONO", channelId: "14b342d1a0fb8a388cf8fba189bb5153" },
  { officialOrder: 28, announcedName: "소이루", channelId: "aaa0a5597d055ab3e33b22207dbb6f24" },
  { officialOrder: 29, announcedName: "송쿤", channelId: "bbd2d3a7abc36bc1d71fab75c35fa0e7" },
  { officialOrder: 30, announcedName: "수녕", channelId: "df4eb2d62da0a45ef8b37b5948126fcb" },
  { officialOrder: 31, announcedName: "시엘 캣츠아이", channelId: "e785798f1e776d12110be5e68daf9873" },
  { officialOrder: 32, announcedName: "아이넬과쇼코", channelId: "11adbdccc0f12e458ce95e0b2be42323" },
  { officialOrder: 33, announcedName: "알로애 Aloe", channelId: "39fa62e81c7d74d9d18610fa6e3d5bdb" },
  { officialOrder: 34, announcedName: "애불", channelId: "592514aff364a66ecbc94a924b5f52c4" },
  { officialOrder: 35, announcedName: "여뭉 toastybear", channelId: "49af53f86b47b0bdf93ffca77d4d801c" },
  { officialOrder: 36, announcedName: "여주 YLIF", channelId: "f8c781ada0626bd8d2d3ed4e403cc375" },
  { officialOrder: 37, announcedName: "옥수햄", channelId: "57e8328f10ac641682a32f0d57c143fe" },
  { officialOrder: 38, announcedName: "우마왕 Woomawang", channelId: "1985d2f95b3438b941f3bc411cba7236" },
  { officialOrder: 39, announcedName: "우유총각", channelId: "6dd1e21513fdf43b36b91b38e205dbed" },
  { officialOrder: 40, announcedName: "유 성운", channelId: "c09abfa1abc8cc94f97810d035ed5e53" },
  { officialOrder: 41, announcedName: "유넬 Yunell", channelId: "fa1b3b9f8927bdc99ac44dabf1f794a9" },
  { officialOrder: 42, announcedName: "유람 Yuram", channelId: "5d68142291810fa1a2a0dfa90a688362" },
  { officialOrder: 43, announcedName: "윤민들레", channelId: "9acde8ff4437d4b751fbfab7a020675e" },
  { officialOrder: 44, announcedName: "이오 군", channelId: "ac80f85cb849fb584c02e2cb2af6be7a" },
  { officialOrder: 45, announcedName: "제미르 ZEMIR", channelId: "96323616ff7dda48c2b1f9a9f940bc39" },
  { officialOrder: 46, announcedName: "제인 Zane", channelId: "f500490b0b9d77ac1ee2dace7f4d208a" },
  { officialOrder: 47, announcedName: "제일리", channelId: "9d865b511f02025292d27b6cb9074c2a" },
  { officialOrder: 48, announcedName: "준갬", channelId: "8d7c970c50889f351e1ce3a8e6dbe93d" },
  { officialOrder: 49, announcedName: "초깨비", channelId: "58fbc220638009db28385db1112d22df" },
  { officialOrder: 50, announcedName: "카든이", channelId: "2a9e91108f2a96e7bd8e685f9b4339a7" },
  { officialOrder: 51, announcedName: "콩쥐 KongJi", channelId: "b1f0c7bf431fa5bbeeab4101f6fe5169" },
  { officialOrder: 52, announcedName: "킹끄끄", channelId: "edbd742b5e23b6b698f531638eb3c7ec" },
  { officialOrder: 53, announcedName: "타키 Taki", channelId: "23388f4bd11195a570b0884464ba5d5c" },
  { officialOrder: 54, announcedName: "펩 코 이", channelId: "786552f2d5b9c6f3507f3d8e0a34288a" },
  { officialOrder: 55, announcedName: "피 네", channelId: "d8a26244f9cfda1a9250583ad7293427" },
  { officialOrder: 56, announcedName: "하안 HAAN", channelId: "bd435a37373e596d583ed6fd2f17d2f3" },
  { officialOrder: 57, announcedName: "해든 YLIF", channelId: "522359c96788ba8e9823799b58e099a0" },
  { officialOrder: 58, announcedName: "휘운", channelId: "5d7f8a961cecff7b7e071f71eaa93593" },
  { officialOrder: 59, announcedName: "흑 주", channelId: "8746f51a23aad61a7cac34ddb1c1d05b" },
  { officialOrder: 60, announcedName: "IM 한결", channelId: "be7db546b3de12f3e1658a68f4949251" },
  { officialOrder: 61, announcedName: "Im 마린", channelId: "81685b7160d757e1b29305dc0a30b9e8" },
  { officialOrder: 62, announcedName: "JiNiE 지니 I 에델리안", channelId: "939efd46507c177fb35f803777602c5b" },
  { officialOrder: 63, announcedName: "K나그네", channelId: "a1a148a62040fa35e609eb9c4e9c5804" },
  { officialOrder: 64, announcedName: "Syze사이즈", channelId: "2c33aeb0435762a95aa829b23e76ef6a" },
];

const GROUPS: QualifierGroup[] = [
  { teamNumber: 1, groupEntryId: "g1", members: [
      { memberOrder: 1, announcedName: "공 운", channelId: "b6475394b5c812be2e712095f8604db2" },
      { memberOrder: 2, announcedName: "김 이 든", channelId: "7b624aeb24c6a6be26eb2dea5b6ac69f" },
      { memberOrder: 3, announcedName: "시 키 Siki", channelId: "e5e69fafebfeaf98a9717dec24a0f6fe" },
    ] },
  { teamNumber: 2, groupEntryId: "g2", members: [
      { memberOrder: 1, announcedName: "구이룬", channelId: "1965959830702dce3b657129dd8a6b8c" },
      { memberOrder: 2, announcedName: "아시모프A158", channelId: "efc60c0f95ce3be7f91e9da4a5f2d99c" },
    ] },
  { teamNumber: 3, groupEntryId: "g3", members: [
      { memberOrder: 1, announcedName: "김니디", channelId: "9116ce342b24d085f3afe73ccc465465" },
      { memberOrder: 2, announcedName: "슈향", channelId: "57482ec9b07718076a1692bc210d5fa0" },
      { memberOrder: 3, announcedName: "이 선", channelId: "cffac6a96b6a2f625db9e6085c40d1c1" },
      { memberOrder: 4, announcedName: "조별하", channelId: "ead28b71f3fdd5e8b52321825217a065" },
    ] },
  { teamNumber: 4, groupEntryId: "g4", members: [
      { memberOrder: 1, announcedName: "꾸이링", channelId: "27b38f9ce948643d639f6abfc2788b15" },
      { memberOrder: 2, announcedName: "시라이 쿄카", channelId: "540369f6aea657761b250a6ee383a406" },
    ] },
  { teamNumber: 5, groupEntryId: "g5", members: [
      { memberOrder: 1, announcedName: "나기 히유라", channelId: "cf6389ea2b91e3a3ef2df8d9ea71c1c5" },
      { memberOrder: 2, announcedName: "아루테미 슈 SHU", channelId: "aec70225ee6f7ce910a26084f95d6f60" },
    ] },
  { teamNumber: 6, groupEntryId: "g6", members: [
      { memberOrder: 1, announcedName: "난 에궁", channelId: "9f673229b644e81373811f7e272b2098" },
      { memberOrder: 2, announcedName: "무개냥", channelId: "8258938ce8070864e5018e43fdbcef2c" },
    ] },
  { teamNumber: 7, groupEntryId: "g7", members: [
      { memberOrder: 1, announcedName: "난바다", channelId: "b1ac916d727d32cb7f472b50f6f97654" },
      { memberOrder: 2, announcedName: "조하구1", channelId: "c9e23f9b2d858601ac5f20f46634cce2" },
      { memberOrder: 3, announcedName: "해득이", channelId: "659dcc02fa42de17387db4c8d722f55c" },
    ] },
  { teamNumber: 8, groupEntryId: "g8", members: [
      { memberOrder: 1, announcedName: "냐페르", channelId: "5d6a0908bb4eac47f3d839cbffe60961" },
      { memberOrder: 2, announcedName: "디아푸", channelId: "50bc69419921ed333fee0a0c854076e5" },
      { memberOrder: 3, announcedName: "선아린", channelId: "5891784ca4e2320cfa25668f1f247579" },
    ] },
  { teamNumber: 9, groupEntryId: "g9", members: [
      { memberOrder: 1, announcedName: "돈도로다단", channelId: "684027b72e41f8895400b1f813c465b6" },
      { memberOrder: 2, announcedName: "버음바", channelId: "71fe514d31377367a83a8bfaf720c783" },
    ] },
  { teamNumber: 10, groupEntryId: "g10", members: [
      { memberOrder: 1, announcedName: "동동씨", channelId: "0baca7f3a4b838eb4e65d2068a189b19" },
      { memberOrder: 2, announcedName: "호잇뚠", channelId: "df5b281407e63b91ca80e90b0cd1afc6" },
    ] },
  { teamNumber: 11, groupEntryId: "g11", members: [
      { memberOrder: 1, announcedName: "디플라", channelId: "3860a8c5c9ea7cf9b7e0f89a5abced9a" },
      { memberOrder: 2, announcedName: "레노떼", channelId: "775961416cd479ee1de7b5aabc73b5ce" },
    ] },
  { teamNumber: 12, groupEntryId: "g12", members: [
      { memberOrder: 1, announcedName: "레드오랭지", channelId: "e0ecdf2c06a74ea5b1fc6b7e8d626dd6" },
      { memberOrder: 2, announcedName: "레츠 Letsu", channelId: "f9666b790723630d4c5e73525f968f94" },
    ] },
  { teamNumber: 13, groupEntryId: "g13", members: [
      { memberOrder: 1, announcedName: "마호노 레비 Orbi", channelId: "dd34d48439b7feee08b53cb4063cb0fd" },
      { memberOrder: 2, announcedName: "모모메이 유메 Orbi", channelId: "3bc90b22f6b7925e5b92767562f44f3b" },
      { memberOrder: 3, announcedName: "코오모리 히키 Orbi", channelId: "cfb55fb02bdcbf17c3eb0bd756ee7762" },
    ] },
  { teamNumber: 14, groupEntryId: "g14", members: [
      { memberOrder: 1, announcedName: "맥문동", channelId: "733c98be047f710d3b1bc7a27b0c83e2" },
      { memberOrder: 2, announcedName: "PROJECT8", channelId: "41db3bd7760c7b813b0f06df1876e1af" },
    ] },
  { teamNumber: 15, groupEntryId: "g15", members: [
      { memberOrder: 1, announcedName: "므므네 mumune", channelId: "23a256033aae1c6324cec9a69458791d" },
      { memberOrder: 2, announcedName: "아일라 Iyla", channelId: "ff182197eed405b1130d22aa3a4fefa0" },
    ] },
  { teamNumber: 16, groupEntryId: "g16", members: [
      { memberOrder: 1, announcedName: "미로 사쿠", channelId: "3c143d7505da6cf4a092d62690460b3f" },
      { memberOrder: 2, announcedName: "트리시kr", channelId: "fd63dc8ed9457fcf190dbd71978e32fa" },
      { memberOrder: 3, announcedName: "폴리모프 P0lym0rph", channelId: "918da8b2f34e8adeb1a32e10e4c1d4e3" },
    ] },
  { teamNumber: 17, groupEntryId: "g17", members: [
      { memberOrder: 1, announcedName: "미사키 하루", channelId: "9f7467f5f3dfa4ea3dcef2962187d6a2" },
      { memberOrder: 2, announcedName: "슨아", channelId: "3fefbab86746791039760a3ce516f1da" },
    ] },
  { teamNumber: 18, groupEntryId: "g18", members: [
      { memberOrder: 1, announcedName: "베이야", channelId: "c4a63fcc6fc5f6c2da7692e6dcbba96a" },
      { memberOrder: 2, announcedName: "에스타", channelId: "b41937fced2370c1912e6949b4c59227" },
    ] },
  { teamNumber: 19, groupEntryId: "g19", members: [
      { memberOrder: 1, announcedName: "사이네코 코나타 Wishing", channelId: "dc5a830020254936843b8afeed98949f" },
      { memberOrder: 2, announcedName: "쿠릉 Kurung", channelId: "ab52ab49739163b108e253c4f038dcec" },
    ] },
  { teamNumber: 20, groupEntryId: "g20", members: [
      { memberOrder: 1, announcedName: "시라하 마노", channelId: "ea852f54a083225ae86ec5df85bdceb5" },
      { memberOrder: 2, announcedName: "아샤 누아르", channelId: "562958e2539ea1e44b93ae2adb51d1a1" },
      { memberOrder: 3, announcedName: "이 치 치", channelId: "cfccab4af1801bfd217076c559650278" },
    ] },
  { teamNumber: 21, groupEntryId: "g21", members: [
      { memberOrder: 1, announcedName: "양담비", channelId: "21e45f2d67f33b783e58a0823ac43d2b" },
      { memberOrder: 2, announcedName: "코우세이 우키", channelId: "1b8fadbb141d246c88a8a722ef3bb06f" },
    ] },
  { teamNumber: 22, groupEntryId: "g22", members: [
      { memberOrder: 1, announcedName: "온유 OnU", channelId: "2f15b1272b730b79a43a0bddf3b5d1dd" },
      { memberOrder: 2, announcedName: "이소 2SO", channelId: "e62b2771a14fb902fba8b3b324d59e45" },
    ] },
  { teamNumber: 23, groupEntryId: "g23", members: [
      { memberOrder: 1, announcedName: "온하얀 ONHAYAN", channelId: "91caa53fc6cf5ee3cdbc802bd23bf155" },
      { memberOrder: 2, announcedName: "유레이 UREI", channelId: "4f650f02bc4ab38a998d74e3abb1b68b" },
      { memberOrder: 3, announcedName: "이루네 IRUNE", channelId: "e984779fd445e71bfd8c99106e432bf1" },
      { memberOrder: 4, announcedName: "하나빈 HANAVIN", channelId: "7ca6c5f45a9b16f75970f54c309623c0" },
    ] },
  { teamNumber: 24, groupEntryId: "g24", members: [
      { memberOrder: 1, announcedName: "요라밍Yoraming", channelId: "06530ce4e3782aa09d80a4989b3b5243" },
      { memberOrder: 2, announcedName: "판구리", channelId: "ed3f2b9e1f3df94090cc0e21bce64519" },
    ] },
  { teamNumber: 25, groupEntryId: "g25", members: [
      { memberOrder: 1, announcedName: "은초이 Choy", channelId: "fd8516eb8d31a8a5147e94c281ae3f07" },
      { memberOrder: 2, announcedName: "조이람", channelId: "e66acc8276b509151dbdc3f9a4477dbc" },
    ] },
  { teamNumber: 26, groupEntryId: "g26", members: [
      { memberOrder: 1, announcedName: "이 로 지", channelId: "e3e50ec6c85c2a286c32acd6da83b697" },
      { memberOrder: 2, announcedName: "하루시카 시나", channelId: "571e7d61e19e43331bc87035dca4b56f" },
    ] },
  { teamNumber: 27, groupEntryId: "g27", members: [
      { memberOrder: 1, announcedName: "이리을 LEERIEUL", channelId: "15025d616518761242b9e05b41cd4cd4" },
      { memberOrder: 2, announcedName: "후츄후츄", channelId: "8b14aeaab59c1896ff9350a4a03515d3" },
    ] },
  { teamNumber: 28, groupEntryId: "g28", members: [
      { memberOrder: 1, announcedName: "초슈야", channelId: "2589896226ea56ac81886a7b46103975" },
      { memberOrder: 2, announcedName: "흑지로", channelId: "61acacd56ff16f2b5cd09aa8b9d5002d" },
    ] },
  { teamNumber: 29, groupEntryId: "g29", members: [
      { memberOrder: 1, announcedName: "치카치카 쵸케", channelId: "573994c85d061e56952d7b2e0483d012" },
      { memberOrder: 2, announcedName: "코네코토 스야", channelId: "076b5984789b249599352ab8bd8420be" },
    ] },
  { teamNumber: 30, groupEntryId: "g30", members: [
      { memberOrder: 1, announcedName: "폰루루 ㅣ PON RuRu", channelId: "051a3090c9566bdfd6af01cb1f463c16" },
    ] },
  { teamNumber: 31, groupEntryId: "g31", members: [
      { memberOrder: 1, announcedName: "한 유 월", channelId: "0feaf00a7322954b2da161d401e09dc8" },
      { memberOrder: 2, announcedName: "RuriHana", channelId: "24eef70586192ec0dcc5df0df2a56b16" },
    ] },
  { teamNumber: 32, groupEntryId: "g32", members: [
      { memberOrder: 1, announcedName: "호시유메 루나", channelId: "162b203fac941457a4b3d4c708051585" },
      { memberOrder: 2, announcedName: "후와리 미토미", channelId: "9db6c061100d0afeaf6ec94dfb3cdb42" },
    ] },
];

export const SINGCUP_QUALIFIERS = {
  seasonKey: "galaxy-2026",
  sourceTitle: "치지직 싱드컵 갤럭시 시즌 🚀 예선 참가자 명단 발표",
  sourceUrl: "https://game.naver.com/lounge/chzzk/board/detail/8057512",
  /** 공지 원문 작성 시각(라운지가 준 형식 그대로). */
  sourceCreatedAt: "20260814000044",
  /** 이 명단을 추출한 시각(UTC). */
  retrievedAt: "2026-08-14T13:47:14Z",
  counts: {
    femaleSolo: 64,
    maleSolo: 64,
    groups: 32,
    /** 인원수가 아니라 **링크 항목 수**다. 위 주석의 표기 규칙을 볼 것. */
    groupMemberLinks: 73,
  },
  femaleSolo: FEMALE_SOLO,
  maleSolo: MALE_SOLO,
  groups: GROUPS,
} as const;

/** 공지의 링크는 201개 전부 이 규칙과 일치한다(테스트로 고정). 그래서 URL 문자열을
 *  201번 싣지 않고 여기서 만든다. */
export function qualifierChannelUrl(channelId: string): string {
  return `https://chzzk.naver.com/${channelId}`;
}

/** 솔로 두 배열을 카테고리 라벨과 함께 한 줄로 편다. 카테고리는 배열 소속으로
 *  정해지므로 항목마다 저장하지 않는다. */
export function allSoloQualifiers(): (QualifierSolo & { category: QualifierCategory })[] {
  return [
    ...FEMALE_SOLO.map((s) => ({ ...s, category: "female_solo" as const })),
    ...MALE_SOLO.map((s) => ({ ...s, category: "male_solo" as const })),
  ];
}

/** 공식 명단에 실린 모든 channel_id(솔로 128 + 그룹 멤버 73). 조인은 **channel_id로만**
 *  한다 — 이름은 발표 이후 바뀌므로 조인 키로 쓸 수 없다. */
export function allQualifierChannelIds(): string[] {
  return [
    ...FEMALE_SOLO.map((s) => s.channelId),
    ...MALE_SOLO.map((s) => s.channelId),
    ...GROUPS.flatMap((g) => g.members.map((m) => m.channelId)),
  ];
}

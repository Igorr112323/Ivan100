/* Основная надпись (штамп) по ГОСТ 21.101, форма 3.
   Поля заполнены настоящими значениями: это подпись листа, а не украшение. */

type Props = {
  title: string;
  sheet: string;
  scale?: string;
  stage?: string;
  note?: string;
};

export default function Stamp({ title, sheet, scale = "1:25 000", stage = "Опытно-промышленная", note }: Props) {
  return (
    <div>
      <div className="stamp">
        <div className="stamp-title">{title}</div>
        <div className="stamp-row">
          <div className="stamp-cell">
            <b>Разраб.</b>
            Шершнев И.А.
          </div>
          <div className="stamp-cell">
            <b>Пров.</b>
            Богус А.Э., к.т.н.
          </div>
          <div className="stamp-cell">
            <b>Масштаб</b>
            {scale}
          </div>
        </div>
        <div className="stamp-row">
          <div className="stamp-cell">
            <b>Шифр</b>
            АКВ-2027-КРА
          </div>
          <div className="stamp-cell">
            <b>Лист</b>
            {sheet}
          </div>
          <div className="stamp-cell">
            <b>Стадия</b>
            {stage}
          </div>
        </div>
      </div>
      {note ? <div className="stamp-foot">{note}</div> : null}
    </div>
  );
}
